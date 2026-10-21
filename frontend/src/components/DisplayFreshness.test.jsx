import React, { act } from "react";
import { createRoot } from "react-dom/client";
import DisplayFreshness from "./DisplayFreshness";

test("a newly retrieved old source keeps its historical source timestamp", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const node = document.createElement("div");
  const root = createRoot(node);
  try {
    await act(async () => root.render(<DisplayFreshness updatedAt={Date.now()} data={{ generated_at: "2020-01-02T17:00:00Z" }} />));
    expect(node.textContent).toContain("DISPLAY LOADED");
    expect(node.textContent).toContain("Source stamped Jan 2");
    expect(node.textContent).not.toContain("LIVE");
  } finally { await act(async () => root.unmount()); }
});
