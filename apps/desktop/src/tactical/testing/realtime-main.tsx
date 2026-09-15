import { createRoot } from "react-dom/client";
import { Workspace } from "../../Workspace";
import { TauriBridgeTransport } from "../../bridge/transport";
import type { CommandPort } from "../../bridge/transport";
import "../../styles.css";
import { TacticalWorkspace } from "../TacticalWorkspace";

// Development-only test host. Browser automation supplies a real Python stdio
// sidecar through this binding; this entry is absent from the production page.
declare global { interface Window { __e3b_request(request: unknown): Promise<unknown> } }
const port: CommandPort = {
  createChannel: () => null,
  invoke: async <T,>(command: string, args?: Record<string, unknown>): Promise<T> => {
    if (command === "bridge_tactical_request" || command === "bridge_editor_request")
      return await window.__e3b_request(args!.request) as T;
    if (command === "bridge_choose_file")
      return await window.__e3b_request({ method: "__choose_file", params: args!.request }) as T;
    throw new Error(`Unsupported test host call: ${command}`);
  },
};
const tacticalEntry = new URLSearchParams(window.location.search).get("entry") === "tactical";
createRoot(document.getElementById("root")!).render(<main className={`app-shell${tacticalEntry ? " tactical-app" : ""}`}>
  {tacticalEntry ? <TacticalWorkspace transport={new TauriBridgeTransport(port)} instance="backend.e3bbrowser" /> :
    <Workspace transport={new TauriBridgeTransport(port)} instance="backend.e3bbrowser" tacticalAvailable />}
</main>);
