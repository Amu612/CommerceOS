import React from "react";
import ReactDOM from "react-dom/client";
import "./theme.css";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { installFetchAuth } from "./lib/api";

installFetchAuth();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
);
