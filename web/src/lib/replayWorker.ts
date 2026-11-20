// Builds the replay's index off the page's thread, for a window past 100 000 events (decision 0019, §3: S3 measured
// 0.8 s for a million). The page posts the window and gets the index back.
import { buildIndex, type ReplayWindow } from "./replay";

self.onmessage = (e: MessageEvent<ReplayWindow>) => {
  (self as unknown as Worker).postMessage(buildIndex(e.data));
};
