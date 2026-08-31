import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Shell from "./components/Shell";
import Dashboard from "./pages/Dashboard";
import InboxPage from "./pages/Inbox";
import AgenticChatPage from "./pages/AgenticChat";
import UploadPage from "./pages/Upload";
import PipelinePage from "./pages/Pipeline";
import EmployeesPage from "./pages/Employees";
import FilesPage from "./pages/Files";
import RemindersPage from "./pages/Reminders";
import ExportPage from "./pages/Export";
import RecordPage from "./pages/Record";
import Login from "./pages/Login";
import AdminSettings from "./pages/admin/Settings";
import AdminUsers from "./pages/admin/Users";
import AdminCalendars from "./pages/admin/Calendars";
import AdminExtractionDebug from "./pages/admin/ExtractionDebug";
import AdminPortalUsers from "./pages/admin/PortalUsers";
import PortalLogin from "./pages/portal/PortalLogin";
import PortalEmployeeUpload from "./pages/portal/PortalEmployeeUpload";
import PortalEmployeeHistory from "./pages/portal/PortalEmployeeHistory";
import { useAuth } from "./lib/auth";
import { PortalAuthProvider, usePortalAuth } from "./lib/portalAuth";
import { UploadSessionProvider } from "./lib/uploadSession";
import { Spinner } from "./components/ui";

// The restricted "vault_matcher" role only ever sees these two pages — every
// other route redirects to /employees. Mirrors the server-side restriction
// (api/deps.require_full_access blocks that role on every other router), so
// this is a UX convenience, not the actual security boundary.
const VAULT_MATCHER_ALLOWED_PREFIXES = ["/employees", "/files"];

function Protected({ children, adminOnly }: { children: JSX.Element; adminOnly?: boolean }) {
  const { user, loading, isAdmin, isVaultMatcherOnly } = useAuth();
  const loc = useLocation();
  if (loading)
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-7 w-7" />
      </div>
    );
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  if (adminOnly && !isAdmin) return <Navigate to="/" replace />;
  if (isVaultMatcherOnly && !VAULT_MATCHER_ALLOWED_PREFIXES.some((p) => loc.pathname.startsWith(p))) {
    return <Navigate to="/employees" replace />;
  }
  return children;
}

// Entirely separate auth from the internal Protected/useAuth above — its own
// token namespace, its own login page, never reachable from the internal nav.
function PortalProtected({ children }: { children: JSX.Element }) {
  const { user, loading } = usePortalAuth();
  if (loading)
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-7 w-7" />
      </div>
    );
  if (!user) return <Navigate to="/portal/login" replace />;
  return children;
}

function PortalRoutes() {
  const { user } = usePortalAuth();
  return (
    <Routes>
      <Route
        path="login"
        element={user ? <Navigate to="/portal/employee" replace /> : <PortalLogin />}
      />
      <Route path="employee" element={<PortalProtected><PortalEmployeeUpload /></PortalProtected>} />
      <Route
        path="employee/history"
        element={<PortalProtected><PortalEmployeeHistory /></PortalProtected>}
      />
    </Routes>
  );
}

export default function App() {
  const { user } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route
        path="/portal/*"
        element={
          <PortalAuthProvider>
            <PortalRoutes />
          </PortalAuthProvider>
        }
      />
      <Route
        path="/*"
        element={
          <Protected>
            {/* Wrapped ABOVE the inner route switch (not inside UploadPage
                itself), so it survives navigating away from /upload and
                back — only the matched route's component unmounts, this
                stays mounted the whole session. See lib/uploadSession.tsx. */}
            <UploadSessionProvider>
            <Shell>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/inbox" element={<InboxPage />} />
                <Route path="/chat" element={<AgenticChatPage />} />
                <Route path="/upload" element={<UploadPage />} />
                <Route path="/pipeline" element={<PipelinePage />} />
                <Route path="/employees" element={<EmployeesPage />} />
                <Route path="/export" element={<ExportPage />} />
                <Route path="/files" element={<FilesPage />} />
                <Route path="/reminders" element={<RemindersPage />} />
                <Route path="/records/:id" element={<RecordPage />} />
                <Route path="/admin/settings" element={<Protected adminOnly><AdminSettings /></Protected>} />
                <Route path="/admin/users" element={<Protected adminOnly><AdminUsers /></Protected>} />
                <Route path="/admin/calendars" element={<Protected><AdminCalendars /></Protected>} />
                <Route path="/admin/portal-users" element={<Protected><AdminPortalUsers /></Protected>} />
                <Route path="/admin/debug" element={<Protected adminOnly><AdminExtractionDebug /></Protected>} />
              </Routes>
            </Shell>
            </UploadSessionProvider>
          </Protected>
        }
      />
    </Routes>
  );
}
