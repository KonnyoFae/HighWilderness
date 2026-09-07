"""W2a: real resource/session invariants and non-blocking sidecar I/O."""
import json
from queue import Queue
from threading import Event, Thread
import unittest

from backend.high_wilderness_sidecar.sessions import EditorService, MAX_SESSIONS
from backend.high_wilderness_sidecar.server import SidecarServer
from 高天荒野舰艇数据契约 import ContractError
from 高天荒野Web桥接协议 import encode_message, validate_message
from 高天荒野WebSidecar生命周期测试 import spawn, hello, request, write, read_line, finish, INSTANCE_ID


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.service = EditorService(INSTANCE_ID)
        self.base = request("req.1", "resource.list", {})
        self.resources = self.call("resource.list")["resources"]
        self.key = next(r["key"] for r in self.resources if r["editable"])
        self.state = self.call("editor.open", {"resource_key": self.key})

    def call(self, method, params=None, revision=None):
        value = dict(self.base, method=method, params=params or {})
        if hasattr(self, "state") and method not in {"resource.list", "editor.open"}:
            value.update(session_id=self.state["session_id"],
                         expected_revision=self.state["revision"] if revision is None else revision)
        return self.service.dispatch(value)[0]

    def rename(self, name):
        self.state = self.call("editor.command", {"command": "hull.rename", "arguments": {"name": name}})

    def test_index_three_hulls_and_no_path_access(self):
        self.assertEqual(len(self.resources), 12)
        for entry in self.resources:
            if entry["editable"]:
                opened = self.call("editor.open", {"resource_key": entry["key"]})
                self.assertTrue(opened["preview"]["valid"])
                self.assertFalse(opened["dirty"])
        with self.assertRaisesRegex(ContractError, "resource_missing"):
            self.call("editor.open", {"resource_key": "../../secret"})
        with self.assertRaisesRegex(ContractError, "invalid_arguments"):
            self.call("editor.open", {"resource_key": self.key, "path": "x"})
        other = next(x for x in self.resources if not x["editable"])
        with self.assertRaisesRegex(ContractError, "kind_not_supported"):
            self.call("editor.open", {"resource_key": other["key"]})

    def test_history_hashes_noop_and_branch(self):
        original = self.state["draft_sha256"]
        self.rename(self.state["draft"]["name"])
        self.assertEqual(self.state["revision"], 0)
        self.rename("试航甲")
        changed = self.state["draft_sha256"]
        self.state = self.call("editor.undo")
        self.assertEqual(self.state["draft_sha256"], original)
        self.assertFalse(self.state["dirty"])
        self.state = self.call("editor.redo")
        self.assertEqual(self.state["draft_sha256"], changed)
        self.state = self.call("editor.undo")
        self.rename("试航乙")
        self.assertFalse(self.state["can_redo"])
        self.assertEqual(self.state["revision"], 5)

    def test_conflict_and_bad_command_atomic_with_inspect_recovery(self):
        self.rename("新名字")
        before = self.call("editor.inspect")
        with self.assertRaisesRegex(ContractError, "revision_conflict"):
            self.call("editor.undo", revision=0)
        with self.assertRaisesRegex(ContractError, "invalid_arguments"):
            self.call("editor.command", {"command": "hull.rename", "arguments": {"name": ""}})
        self.assertEqual(self.call("editor.inspect", revision=0), before)
        self.assertEqual(self.call("editor.preview"), before)

    def test_illegal_draft_retains_last_valid_preview(self):
        valid = self.state["preview"]
        self.state = self.call("editor.command", {"command": "hull.set_structure_material", "arguments": {
            "deck_id": self.state["draft"]["decks"][0]["id"],
            "material": {"id": "missing.material", "version": 1},
        }})
        self.assertFalse(self.state["preview"]["valid"])
        self.assertTrue(self.state["preview"]["diagnostics"])
        self.assertEqual(self.state["last_valid_preview"], valid)
        self.assertEqual(self.state["last_valid_revision"], 0)
        self.state = self.call("editor.undo")
        self.assertTrue(self.state["preview"]["valid"])

    def test_detached_results_dirty_close_and_session_limit(self):
        self.state["draft"]["name"] = "外部对象篡改"
        self.assertNotEqual(self.call("editor.inspect")["draft"]["name"], "外部对象篡改")
        self.rename("未保存")
        with self.assertRaisesRegex(ContractError, "dirty_session"):
            self.call("editor.close", {"discard_changes": False})
        self.assertTrue(self.call("editor.close", {"discard_changes": True})["closed"])
        with self.assertRaisesRegex(ContractError, "session_missing"):
            self.call("editor.inspect")
        for _ in range(MAX_SESSIONS):
            self.call("editor.open", {"resource_key": self.key})
        with self.assertRaisesRegex(ContractError, "session_limit"):
            self.call("editor.open", {"resource_key": self.key})

    def test_real_process_open_edit_undo_conflict_and_shutdown(self):
        proc = spawn()
        try:
            write(proc, hello())
            self.assertIn("editor.command", read_line(proc.stdout)["result"]["capabilities"])
            read_line(proc.stdout)
            write(proc, request("req.2", "editor.open", {"resource_key": self.key}))
            opened = read_line(proc.stdout)
            validate_message(opened)
            state = opened["result"]
            self.assertIsNone(opened["session_id"])
            args = {"command": "hull.rename", "arguments": {"name": "真实进程试航"}}
            write(proc, request("req.3", "editor.command", args,
                                session_id=state["session_id"], expected_revision=0))
            edited = read_line(proc.stdout)
            self.assertEqual(edited["revision"], 1)
            write(proc, request("req.4", "editor.undo", {}, session_id=state["session_id"], expected_revision=0))
            conflict = read_line(proc.stdout)
            self.assertEqual(conflict["error"]["code"], "editor.revision_conflict")
            self.assertEqual(conflict["revision"], 1)
            write(proc, request("req.5", "editor.undo", {}, session_id=state["session_id"], expected_revision=1))
            self.assertEqual(read_line(proc.stdout)["result"]["draft_sha256"], state["draft_sha256"])
            write(proc, request("req.6", "system.shutdown", {"reason": "user_exit"}))
            self.assertTrue(read_line(proc.stdout)["ok"])
            self.assertEqual(finish(proc, 0), "")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()

    def test_heartbeat_and_queue_overflow_during_blocked_domain_work(self):
        incoming, outgoing = Queue(), Queue()
        entered, release = Event(), Event()

        class Input:
            def read1(self, size):
                return incoming.get(timeout=10)

        class Output:
            def write(self, data):
                outgoing.put(json.loads(data))
            def flush(self):
                pass

        server = SidecarServer(INSTANCE_ID)
        original = server.editor.dispatch
        def blocked(message):
            entered.set()
            self.assertTrue(release.wait(10))
            return original(message)
        server.editor.dispatch = blocked
        codes = []
        thread = Thread(target=lambda: codes.append(server.serve(Input(), Output())), daemon=True)
        thread.start()
        try:
            incoming.put(encode_message(hello()))
            self.assertTrue(outgoing.get(timeout=5)["ok"])
            outgoing.get(timeout=5)
            incoming.put(encode_message(request("req.2", "resource.list", {})))
            self.assertTrue(entered.wait(5))
            for n in range(3, 12):
                incoming.put(encode_message(request(f"req.{n}", "resource.list", {})))
            busy = outgoing.get(timeout=5)
            self.assertEqual(busy["error"]["code"], "bridge.busy")
            incoming.put(encode_message(request("req.12", "system.ping", {"nonce": "ping.alive"})))
            self.assertEqual(outgoing.get(timeout=5)["result"], {"nonce": "ping.alive"})
        finally:
            release.set()
            incoming.put(b"")
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(codes, [0])
        accepted = [outgoing.get(timeout=5) for _ in range(9)]
        self.assertEqual([x["request_id"] for x in accepted], [f"req.{n}" for n in range(2, 11)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
