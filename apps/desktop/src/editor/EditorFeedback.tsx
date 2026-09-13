import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";

import { createPortal } from "react-dom";

const FeedbackContext = createContext<HTMLElement | null>(null);

export function EditorFeedback({ children, status }: { children: ReactNode; status: ReactNode }) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  return <FeedbackContext.Provider value={target}>
    {children}
    <section className="editor-feedback" aria-label="操作提示窗口">
      <header>操作提示 <span>操作结果与当前工具</span></header>
      <div className="editor-feedback-scroll" role="log" aria-live="polite" aria-relevant="additions text">
        <div className="feedback-session">{status}</div><div ref={setTarget} />
      </div>
    </section>
  </FeedbackContext.Provider>;
}

export function EditorNotice({ children }: { children: ReactNode }) {
  const target = useContext(FeedbackContext);
  return target ? createPortal(<div className="feedback-entry">{children}</div>, target) : null;
}
