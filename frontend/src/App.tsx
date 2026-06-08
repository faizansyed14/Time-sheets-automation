import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import Upload from "./pages/Upload";
import Files from "./pages/Files";
import EmployeeMatcher from "./pages/EmployeeMatcher";
import EmployeeMonth from "./pages/EmployeeMonth";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/files" element={<Files />} />
        <Route path="/employee-matcher" element={<EmployeeMatcher />} />
        <Route path="/employee/:pk" element={<EmployeeMonth />} />
      </Routes>
    </Layout>
  );
}
