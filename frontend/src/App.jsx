import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { AuthProvider, useAuth } from "./context/AuthContext";
import { WorkspaceProvider } from "./context/WorkspaceContext";
import AppShell from "./components/AppShell";
import { LoadingState } from "./components/States";

import Home from "./pages/Home";
import About from "./pages/About";
import Login from "./pages/Login";
import ResetPassword from "./pages/ResetPassword";
import Dashboard from "./pages/Dashboard";
import MapPage from "./pages/MapPage";
import Survey from "./pages/Survey";
import Assignment from "./pages/survey/Assignment";
import Datasets from "./pages/survey/Datasets";
import Processing from "./pages/survey/Processing";
import Review from "./pages/survey/Review";
import Analytics from "./pages/survey/Analytics";
import Export from "./pages/survey/Export";

/**
 * Everything under this route needs a signed-in surveyor. Without a
 * session the user is sent to /login and brought back afterwards.
 */
function ProtectedLayout() {
  const { loading, authenticated } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="app-loading">
        <LoadingState label="Checking your session" />
      </div>
    );
  }
  if (!authenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return (
    <WorkspaceProvider>
      <AppShell>
        <Outlet />
      </AppShell>
    </WorkspaceProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/about" element={<About />} />
          <Route path="/login" element={<Login />} />
          <Route path="/reset-password" element={<ResetPassword />} />

          <Route element={<ProtectedLayout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/map" element={<MapPage />} />
            <Route path="/survey" element={<Survey />}>
              <Route index element={<Assignment />} />
              <Route path="datasets" element={<Datasets />} />
              <Route path="processing" element={<Processing />} />
              <Route path="review" element={<Review />} />
              <Route path="analytics" element={<Analytics />} />
              <Route path="export" element={<Export />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
