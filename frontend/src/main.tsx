import React from "react";
import { createRoot } from "react-dom/client";
import { GoogleOAuthProvider } from "@react-oauth/google";
import "./styles/tokens.css";
import App from "./App";

// Vite only exposes env vars prefixed VITE_ to client-side code (anything
// else stays server/build-only, so secrets never leak into the bundle by
// accident) — set this in frontend/.env as VITE_GOOGLE_CLIENT_ID=...,
// same Client ID used on the backend's GOOGLE_CLIENT_ID.
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <App />
    </GoogleOAuthProvider>
  </React.StrictMode>,
);
