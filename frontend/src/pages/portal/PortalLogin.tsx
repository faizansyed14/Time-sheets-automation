import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, FolderLock } from "lucide-react";
import { portalLogin } from "../../api/client";
import { usePortalAuth } from "../../lib/portalAuth";
import { Button, Input, Field } from "../../components/ui";

export default function PortalLogin() {
  const { setSession } = usePortalAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await portalLogin(username.trim(), password);
      setSession(res.access_token, res.user);
      navigate("/portal/employee", { replace: true });
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Incorrect username or password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200/80 bg-white p-8 shadow-card">
        <div className="mb-6 flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white">
            <FolderLock className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-slate-900">Timesheet Portal</h1>
            <p className="text-xs text-slate-500">Employee sign-in</p>
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Username">
            <Input
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            Sign in
          </Button>
        </form>
        <p className="mt-6 text-center text-xs text-slate-400">
          Don't have an account? Ask your timesheet team to create one for you.
        </p>
      </div>
    </div>
  );
}
