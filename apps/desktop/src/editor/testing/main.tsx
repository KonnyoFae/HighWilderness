import { createRoot } from "react-dom/client";
import { EditorPanel } from "../EditorPanel";
import { fixtureTransport } from "./fixtureTransport";
import "../../styles.css";
createRoot(document.getElementById("root")!).render(<main className="app-shell"><h1>W3a 画布验收夹具</h1><EditorPanel transport={fixtureTransport} instance="fixture.1" /></main>);
