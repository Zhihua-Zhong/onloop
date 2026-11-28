import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ApiError } from "./lib/api";
import { setMarksClient } from "./lib/marks";
// Inter, self-hosted: its woff2 files are built into the wheel, never fetched from a CDN (the CSP allows fonts from
// this origin only). The browser loads only the subsets a page uses.
import "@fontsource-variable/inter";
import "./styles/index.css";

// A refused read (a 4xx: a filter that is not valid) is shown at once, not retried; a failed one is retried once.
const client = new QueryClient({
  defaultOptions: { queries: { retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status < 500), refetchOnWindowFocus: false } },
});

setMarksClient(client);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
