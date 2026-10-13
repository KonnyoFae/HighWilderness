def execute(self, message: dict) -> tuple[tuple[dict[str, Any], ...], bool]:
    method = message["method"]

    if not self.handshake_complete:
        if method != "system.hello":
            error = _bridge_error("handshake_required", "$.method", "首条请求必须是 system.hello")
            return (response_for(message, error=_error_payload(error)),), True
        try:
            result = hello_result(message, ("system.hello", "system.ping", "system.shutdown", *EDITOR_CAPABILITIES, *TACTICAL_CAPABILITIES, *REALTIME_CAPABILITIES, *PREPARATION_CAPABILITIES, RESET_CAPABILITY))
        except ContractError as error:
            return (response_for(message, error=_error_payload(error)),), True
        self.handshake_complete = True
        return (
            response_for(message, result=result),
            self._event(
                "system.ready",
                {
                    "selected_interface": BRIDGE_INTERFACE,
                    "sidecar_interface": SIDECAR_INTERFACE,
                },
            ),
        ), False

    if method == "system.hello":
        error = _bridge_error("handshake_repeated", "$.method", "握手只能执行一次")
        return (response_for(message, error=_error_payload(error)),), True
    if method == "system.ping":
        params = _exact_params(message, {"nonce"})
        if not isinstance(params["nonce"], str) or not ID_PATTERN.fullmatch(params["nonce"]):
            raise _bridge_error("invalid_message", "$.params.nonce", "ping nonce 必须是合法标识")
        return (response_for(message, result={"nonce": params["nonce"]}),), False
    if method == "system.shutdown":
        params = _exact_params(message, {"reason"})
        if params["reason"] not in SHUTDOWN_REASONS:
            raise _bridge_error("invalid_message", "$.params.reason", "未知关闭原因")
        return (response_for(message, result={"accepted": True}),), True

    if method == RESET_CAPABILITY:
        try:
            if message['session_id'] is not None or message['expected_revision'] is not None:
                raise ContractError('reset.scope', '$', '测试重置不绑定编辑会话')
            return (response_for(message, result=reset_tactical_test_state(self, message['params'])),), False
        except ContractError as error:
            return (response_for(message, error=_error_payload(error)),), False

    if method in PREPARATION_CAPABILITIES:
        try:
            if self.tactical.mode != 'editor':
                raise ContractError('preparation.mode', '$', '请先切换到战前准备视图')
            return (response_for(message,result=self.preparation.dispatch(message)),),False
        except ContractError as error:
            return (response_for(message,error=_error_payload(error)),),False

    if method in REALTIME_CAPABILITIES:
        try:
            if method in ('tactical.realtime.create', 'tactical.realtime.deploy', 'tactical.realtime.deploy_prepared', 'tactical.realtime.deploy_encounter'):
                if self.tactical.scenario is not None:
                    raise ContractError('tactical.realtime.scene_active', '$', '请先释放原试航场景')
            return (response_for(message, result=self.realtime.dispatch(message, mode=self.tactical.mode)),), False
        except ContractError as error:
            return (response_for(message, error=_error_payload(error)),), False

    if method in TACTICAL_CAPABILITIES:
        try:
            if method == 'tactical.set_mode':
                self.realtime.pause('mode_exit')
            if method == 'tactical.create' and self.realtime.scheduler is not None:
                raise ContractError('tactical.realtime.scene_active', '$', '请先释放实时试航场景')
            return (response_for(message, result=self.tactical.dispatch(message)),), False
        except ContractError as error:
            return (response_for(message, error=_error_payload(error)),), False

    if method in (*EDITOR_CAPABILITIES, "editor.bind_file"):
        try:
            if self.tactical.mode != "editor" and method not in {
                "resource.list", "editor.inspect", "editor.preview", "editor.recovery_list",
            }:
                raise ContractError("tactical.editor_locked", "$.method", "请先返回编辑视图，再修改设计或保存文件")
            result, revision = self.editor.dispatch(message)
            return (response_for(message, result=result, revision=revision),), False
        except ContractError as error:
            revision = self.editor.current_revision(message["session_id"])
            return (response_for(message, error=_error_payload(error), revision=revision),), False

    error = _bridge_error("method_not_supported", "$.method", f"未启用能力：{method}")
    return (response_for(message, error=_error_payload(error)),), False


def serve(self, input_stream: BinaryIO, output_stream: BinaryIO) -> int:
    # At most eight 8 MiB input frames and sixteen output frames are retained.
    # Only the domain worker touches sessions. Reader handles heartbeat directly.
    jobs: Queue = Queue(maxsize=8)
    outgoing: Queue = Queue(maxsize=16)
    writer_errors: list[BaseException] = []

    def write_outputs() -> None:
        while True:
            outputs = outgoing.get()
            try:
                if outputs is None:
                    return
                if not writer_errors:
                    for output in outputs:
                        output_stream.write(encode_message(output))
                    output_stream.flush()
            except (OSError, ContractError) as error:
                writer_errors.append(error)
            finally:
                outgoing.task_done()

    def work() -> None:
        while True:
            self.realtime.tick()
            try:
                # Service queued input between bounded pumps, but do not
                # add an idle OS wait while committed simulation work is due.
                message = jobs.get(timeout=0 if self.realtime.work_pending or self.tactical.advancing else .002 if self.realtime.running else None)
            except Empty:
                # Bounded preview advances on the authority thread; renders/reads
                # do not drive simulation. Check pause and other jobs every step.
                self.tactical.advance_one()
                continue
            try:
                if message is None:
                    self.realtime.pause('disconnected')
                    return
                try:
                    outputs, _ = self.execute(message)
                except Exception as error:
                    # Preserve queue liveness without leaking internal exception data.
                    failure = _bridge_error("domain_worker_failed", "$", "操作失败，请重新读取当前会话或场景")
                    write_failure_log(failure)
                    outputs = (response_for(message, error=_error_payload(failure)),)
                while True:
                    try:
                        outgoing.put(outputs, timeout=.01)
                        break
                    except Full:
                        self.realtime.pause('disconnected')
            finally:
                jobs.task_done()

    writer = Thread(target=write_outputs, name="sidecar-output", daemon=True)
    worker = Thread(target=work, name="sidecar-domain", daemon=True)
    writer.start()
    worker.start()
    decoder = JsonLineDecoder()
    exit_code = 0
    try:
        while True:
            chunk = input_stream.read1(READ_CHUNK_BYTES)
            if not chunk:
                decoder.finish()
                break
            for raw in decoder.feed(chunk):
                message = self.accept(raw)
                if self.handshake_complete and message["method"] in (*EDITOR_CAPABILITIES, "editor.bind_file", *TACTICAL_CAPABILITIES, *REALTIME_CAPABILITIES, *PREPARATION_CAPABILITIES, RESET_CAPABILITY):
                    try:
                        jobs.put_nowait(message)
                    except Full:
                        error = _bridge_error("busy", "$.method", "操作队列已满，请稍后重新读取")
                        outgoing.put((response_for(message, error=_error_payload(error)),))
                    continue
                # Shutdown stops background advancement on its authority thread.
                if message["method"] == "system.shutdown":
                    jobs.put(None)
                    jobs.join()
                    worker.join()
                try:
                    outputs, should_stop = self.execute(message)
                except FatalProtocolError:
                    raise
                except ContractError as error:
                    outputs = (response_for(message, error=_error_payload(error)),)
                    should_stop = False
                outgoing.put(outputs)
                if should_stop:
                    return 0 if outputs[0].get("ok") is True else 2
    except ContractError as error:
        write_failure_log(error)
        exit_code = 2
    finally:
        if worker.is_alive():
            jobs.put(None)
            worker.join()
        outgoing.put(None)
        writer.join()
    return 2 if writer_errors else exit_code
