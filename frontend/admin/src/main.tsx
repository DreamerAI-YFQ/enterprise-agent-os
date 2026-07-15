import React from "react";
import ReactDOM from "react-dom/client";
import "@eaos/shared/design/tokens.css";
import "@eaos/shared/design/tokens-dark.css";
import { initTheme, initI18n } from "@eaos/shared";
import "./index.css";
import App from "./App";

initTheme();
initI18n();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
