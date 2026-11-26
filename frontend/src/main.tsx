import "./index.css";

import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { Layout, PaddedPage } from "./components/Layout";
import { ApiError } from "./lib/api";
import { AuthGate } from "./lib/auth";
import { AuditPage } from "./pages/AuditPage";
import { BatchPage } from "./pages/BatchPage";
import { BatchesPage } from "./pages/BatchesPage";
import { ChatPage } from "./pages/ChatPage";
import { JobPage } from "./pages/JobPage";
import { JobsPage } from "./pages/JobsPage";
import { MemoryPage } from "./pages/MemoryPage";
import { ReceiptPage } from "./pages/ReceiptPage";
import { StockPage } from "./pages/StockPage";

const queryClient: QueryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
  queryCache: new QueryCache({
    // Сессия истекла — перепроверяем, кто вошёл; AuthGate покажет форму входа.
    onError: (error, query) => {
      if (error instanceof ApiError && error.status === 401 && query.queryKey[0] !== "me") {
        void queryClient.invalidateQueries({ queryKey: ["me"] });
      }
    },
  }),
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthGate>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Navigate to="/chat" replace />} />
              <Route path="chat/:id?" element={<ChatPage />} />
              <Route element={<PaddedPage />}>
                <Route path="memory" element={<MemoryPage />} />
                <Route path="batches" element={<BatchesPage />} />
                <Route path="batches/:id" element={<BatchPage />} />
                <Route path="receipts/new" element={<ReceiptPage />} />
                <Route path="stock" element={<StockPage />} />
                <Route path="jobs" element={<JobsPage />} />
                <Route path="jobs/:id" element={<JobPage />} />
                <Route path="audit" element={<AuditPage />} />
                <Route path="*" element={<p>Страница не найдена.</p>} />
              </Route>
            </Route>
          </Routes>
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
