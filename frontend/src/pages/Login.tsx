import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap, Loader2, RefreshCw, ShieldCheck, KeyRound } from "lucide-react";
import {
  authLogin,
  authResendOtp,
  authVerifyCaptcha,
  authVerifyOtp,
  captchaUrl,
  type LoginResult,
} from "../api/client";
import { useAuth } from "../lib/auth";
import { Button, Input } from "../components/ui";
import { useToast } from "../components/toast";

type Stage = "password" | "otp" | "captcha";

export default function Login() {
  const nav = useNavigate();
  const { setSession } = useAuth();
  const { toast } = useToast();

  const [stage, setStage] = useState<Stage>("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 2FA state
  const [loginToken, setLoginToken] = useState("");
  const [otp, setOtp] = useState("");
  const [debugOtp, setDebugOtp] = useState<string | null>(null);
  const [captchaId, setCaptchaId] = useState("");
  const [captchaImg, setCaptchaImg] = useState("");
  const [captchaAns, setCaptchaAns] = useState("");

  const finish = (token: string, user: any) => {
    setSession(token, user);
    nav("/", { replace: true });
  };

  const onPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res: LoginResult = await authLogin(username.trim(), password);
      if (res.status === "authenticated" && res.access_token) {
        finish(res.access_token, res.user);
      } else if (res.status === "otp_required") {
        setLoginToken(res.login_token!);
        setDebugOtp(res.debug_otp ?? null);
        setStage("otp");
        toast("info", "Verification code sent", res.message ?? undefined);
      } else if (res.status === "captcha_required") {
        setLoginToken(res.login_token!);
        setStage("captcha");
        // refreshCaptcha fetches the image AND captures the id from the response header
        // so they are always in sync. DO NOT use res.captcha_id here — that image was
        // never returned to the client (backend discards the PNG on /login).
        await refreshCaptcha();
      }
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Login failed");
    } finally {
      setBusy(false);
    }
  };

  const onOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res = await authVerifyOtp(loginToken, otp.trim());
      finish(res.access_token, res.user);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Verification failed");
    } finally {
      setBusy(false);
    }
  };

  const onResend = async () => {
    try {
      const res = await authResendOtp(loginToken);
      setDebugOtp(res.debug_otp ?? null);
      toast("success", "New code sent");
    } catch (e: any) {
      toast("error", "Could not resend", e?.response?.data?.detail ?? "");
    }
  };

  const refreshCaptcha = async () => {
    // GET /auth/captcha returns a fresh image + id in the X-Captcha-Id header.
    const r = await fetch(captchaUrl());
    const id = r.headers.get("x-captcha-id") || "";
    const blob = await r.blob();
    setCaptchaId(id);
    setCaptchaImg(URL.createObjectURL(blob));
    setCaptchaAns("");
  };

  const onCaptcha = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res = await authVerifyCaptcha(loginToken, captchaId, captchaAns.trim());
      finish(res.access_token, res.user);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Incorrect CAPTCHA");
      refreshCaptcha();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-grid flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-sm animate-fade-up">
        <div className="mb-6 flex flex-col items-center gap-2.5 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-600 shadow-card ring-1 ring-brand-700/20">
            <Zap className="h-6 w-6 text-white" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">TimeSight</h1>
          <p className="text-xs text-slate-500">Timesheet Intelligence Portal</p>
        </div>

        <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-pop">
          <div className="mb-5 border-b border-slate-100 pb-4">
            <h2 className="text-sm font-semibold text-slate-800">Sign in to your account</h2>
            <p className="mt-0.5 text-xs text-slate-500">Use your administrator-issued credentials.</p>
          </div>
          {err && (
            <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {err}
            </div>
          )}

          {stage === "password" && (
            <form onSubmit={onPassword} className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-semibold text-slate-500">Username</label>
                <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus placeholder="admin" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold text-slate-500">Password</label>
                <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
              </div>
              <Button className="w-full" disabled={busy || !username || !password}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
                Sign in
              </Button>
            </form>
          )}

          {stage === "otp" && (
            <form onSubmit={onOtp} className="space-y-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                <ShieldCheck className="h-4 w-4 text-brand-600" /> Enter verification code
              </div>
              {debugOtp && (
                <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  Dev mode — code: <b className="font-mono">{debugOtp}</b>
                </p>
              )}
              <Input
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                placeholder="6-digit code"
                className="text-center text-lg tracking-[0.5em]"
                autoFocus
              />
              <Button className="w-full" disabled={busy || otp.length < 4}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Verify
              </Button>
              <div className="flex justify-between text-xs">
                <button type="button" onClick={() => setStage("password")} className="text-slate-500 hover:text-slate-700">
                  ← Back
                </button>
                <button type="button" onClick={onResend} className="font-medium text-brand-600 hover:text-brand-700">
                  Resend code
                </button>
              </div>
            </form>
          )}

          {stage === "captcha" && (
            <form onSubmit={onCaptcha} className="space-y-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                <ShieldCheck className="h-4 w-4 text-brand-600" /> Solve the CAPTCHA
              </div>
              <div className="flex items-center gap-2">
                <img src={captchaImg} alt="captcha" className="h-[70px] flex-1 rounded-lg border border-slate-200" />
                <button type="button" onClick={refreshCaptcha} className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50" title="Refresh">
                  <RefreshCw className="h-4 w-4" />
                </button>
              </div>
              <Input
                value={captchaAns}
                onChange={(e) => setCaptchaAns(e.target.value.toUpperCase())}
                placeholder="Type the characters"
                className="text-center tracking-[0.3em]"
                autoFocus
              />
              <Button className="w-full" disabled={busy || !captchaAns}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Verify
              </Button>
              <button type="button" onClick={() => setStage("password")} className="text-xs text-slate-500 hover:text-slate-700">
                ← Back
              </button>
            </form>
          )}
        </div>
        <p className="mt-4 text-center text-[11px] text-slate-400">
          Default admin: <span className="font-mono text-slate-500">admin / admin</span> · change in .env
        </p>
      </div>
    </div>
  );
}
