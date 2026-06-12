import { Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import Dashboard from "./pages/Dashboard";
import InboxPage from "./pages/Inbox";
import UploadPage from "./pages/Upload";
import PipelinePage from "./pages/Pipeline";
import EmployeesPage from "./pages/Employees";
import FilesPage from "./pages/Files";
import RecordPage from "./pages/Record";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/pipeline" element={<PipelinePage />} />
        <Route path="/employees" element={<EmployeesPage />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/records/:id" element={<RecordPage />} />
      </Routes>
    </Shell>
  );
}
