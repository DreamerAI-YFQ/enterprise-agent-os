import { createBrowserRouter, Navigate } from "react-router-dom";
import Login from "./pages/login";
import Chat from "./pages/chat";
import Tasks from "./pages/tasks";
import Notifications from "./pages/notifications";
import Memory from "./pages/memory";
import Knowledge from "./pages/knowledge";
import Bi from "./pages/bi";
import Skills from "./pages/skills";
import Settings from "./pages/settings";
import { AppLayout } from "./layouts/app-layout";
import { ProtectedRoute, PublicOnlyRoute } from "@eaos/shared";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/app" replace />,
  },
  {
    path: "/login",
    element: (
      <PublicOnlyRoute>
        <Login />
      </PublicOnlyRoute>
    ),
  },
  {
    path: "/app",
    element: (
      <ProtectedRoute>
        <AppLayout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Chat /> },
      {
        path: "tasks",
        element: <Tasks />,
      },
      {
        path: "notifications",
        element: <Notifications />,
      },
      {
        path: "memory",
        element: <Memory />,
      },
      {
        path: "skills",
        element: <Skills />,
      },
      {
        path: "knowledge",
        element: <Knowledge />,
      },
      {
        path: "bi",
        element: <Bi />,
      },
      {
        path: "settings",
        element: <Settings />,
      },
    ],
  },
  { path: "*", element: <Navigate to="/app" replace /> },
]);
