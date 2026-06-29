import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Shell from "./components/Shell";
import Dashboard from "./pages/Dashboard";
import InboxPage from "./pages/Inbox";
import AgenticChatPage from "./pages/AgenticChat";
import UploadPage from "./pages/Upload";
import PipelinePage from "./pages/Pipeline";
import EmployeesPage from "./pages/Employees";
import FilesPage from "./pages/Files";
import RecordPage from "./pages/Record";
import Login from "./pages/Login";
import AdminSettings from "./pages/admin/Settings";
import AdminUsers from "./pages/admin/Users";
import { useAuth } from "./lib/auth";
import { Spinner } from "./components/ui";

function Protected({ children, adminOnly }: { children: JSX.Element; adminOnly?: boolean }) {
  const { user, loading, isAdmin } = useAuth();
  const loc = useLocation();
  if (loading)
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-7 w-7" />
      </div>
    );
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  if (adminOnly && !isAdmin) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  const { user } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route
        path="/*"
        element={
          <Protected>
            <Shell>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/inbox" element={<InboxPage />} />
                <Route path="/chat" element={<AgenticChatPage />} />
                <Route path="/upload" element={<UploadPage />} />
                <Route path="/pipeline" element={<PipelinePage />} />
                <Route path="/employees" element={<EmployeesPage />} />
                <Route path="/files" element={<FilesPage />} />
                <Route path="/records/:id" element={<RecordPage />} />
                <Route path="/admin/settings" element={<Protected adminOnly><AdminSettings /></Protected>} />
                <Route path="/admin/users" element={<Protected adminOnly><AdminUsers /></Protected>} />
              </Routes>
            </Shell>
          </Protected>
        }
      />
    </Routes>
  );
}
