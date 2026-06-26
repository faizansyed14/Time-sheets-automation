import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Zap, Loader2, RefreshCw, ShieldCheck, KeyRound, Smartphone } from "lucide-react";
import {
  authLogin,
  authResendOtp,
  authVerifyOtp,
  authVerifyTotp,
  captchaUrl,
  type LoginResult,
} from "../api/client";
import { useAuth } from "../lib/auth";
import { Button, Input } from "../components/ui";
import { useToast } from "../components/toast";

type Stage = "credentials" | "otp" | "totp";

export default function Login() {
  const nav = useNavigate();
  const { setSession } = useAuth();
  const { toast } = useToast();

  const CAPTCHA_LOCK_UNTIL_KEY = "ts:captcha_locked_until_ms";

  const [stage, setStage] = useState<Stage>("credentials");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [loginToken, setLoginToken] = useState("");
  const [otp, setOtp] = useState("");
  const [totp, setTotp] = useState("");
  const [debugOtp, setDebugOtp] = useState<string | null>(null);
  const [totpQr, setTotpQr] = useState<string | null>(null);
  const [totpEnrolling, setTotpEnrolling] = useState(false);

  const [captchaId, setCaptchaId] = useState("");
  const [captchaImg, setCaptchaImg] = useState("");
  const [captchaAns, setCaptchaAns] = useState("");
  const [captchaTick, setCaptchaTick] = useState(0);

  useEffect(() => {
    refreshCaptcha();
  }, []);

  const captchaLockedSec = (() => {
    void captchaTick;
    const raw = sessionStorage.getItem(CAPTCHA_LOCK_UNTIL_KEY);
    const until = raw ? parseInt(raw, 10) || 0 : 0;
    if (!until) return 0;
    const sec = Math.ceil((until - Date.now()) / 1000);
    if (sec <= 0) {
      sessionStorage.removeItem(CAPTCHA_LOCK_UNTIL_KEY);
      return 0;
    }
    return sec;
  })();

  useEffect(() => {
    if (captchaLockedSec <= 0) return;
    const t = window.setInterval(() => setCaptchaTick((n: number) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [captchaLockedSec > 0]);

  const finish = (token: string, user: any) => {
    setSession(token, user);
    nav("/", { replace: true });
  };

  const refreshCaptcha = async () => {
    if (captchaLockedSec > 0) return;
    const r = await fetch(captchaUrl());
    if (r.status === 429) {
      const fromHeader = parseInt(r.headers.get("retry-after") || "", 10);
      const retry = fromHeader > 0 ? fromHeader : 300;
      sessionStorage.setItem(CAPTCHA_LOCK_UNTIL_KEY, String(Date.now() + retry * 1000));
      setCaptchaTick((n: number) => n + 1);
      setCaptchaId("");
      setCaptchaImg("");
      setCaptchaAns("");
      setErr(`Too many requests. Try again in ${retry} seconds.`);
      return;
    }
    if (!r.ok) {
      setErr("Could not load CAPTCHA. Try again.");
      return;
    }
    const id = r.headers.get("x-captcha-id") || "";
    const blob = await r.blob();
    setCaptchaId(id);
    setCaptchaImg(URL.createObjectURL(blob));
    setCaptchaAns("");
  };

  const handleLoginResult = async (res: LoginResult) => {
    if (res.status === "authenticated" && res.access_token) {
      finish(res.access_token, res.user);
    } else if (res.status === "otp_required") {
      setLoginToken(res.login_token!);
      setDebugOtp(res.debug_otp ?? null);
      setStage("otp");
      toast("info", "Verification code sent", res.message ?? undefined);
    } else if (res.status === "totp_required") {
      setLoginToken(res.login_token!);
      setTotpEnrolling(false);
      setTotpQr(null);
      setStage("totp");
    } else if (res.status === "totp_enrollment_required") {
      setLoginToken(res.login_token!);
      setTotpEnrolling(true);
      setTotpQr(res.totp_qr_png ?? null);
      setStage("totp");
      toast("info", "Set up authenticator", res.message ?? undefined);
    }
  };

  const onCredentials = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res = await authLogin(username.trim(), password, captchaId, captchaAns.trim());
      await handleLoginResult(res);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Login failed");
      refreshCaptcha();
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

  const onTotp = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res = await authVerifyTotp(loginToken, totp.trim());
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

  const backToCredentials = () => {
    setStage("credentials");
    setLoginToken("");
    setOtp("");
    setTotp("");
    setTotpQr(null);
    setTotpEnrolling(false);
    refreshCaptcha();
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

          {stage === "credentials" && (
            <form onSubmit={onCredentials} className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-semibold text-slate-500">Username</label>
                <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus placeholder="admin" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold text-slate-500">Password</label>
                <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold text-slate-500">CAPTCHA</label>
                {captchaLockedSec > 0 ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-5 text-center">
                    <p className="text-sm font-semibold text-amber-800">Too many requests</p>
                    <p className="mt-2 font-mono text-3xl font-bold tabular-nums text-amber-900">
                      {captchaLockedSec}s
                    </p>
                    <p className="mt-2 text-xs text-amber-700">Wait, then try again.</p>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      {captchaImg ? (
                        <img src={captchaImg} alt="captcha" className="h-[70px] flex-1 rounded-lg border border-slate-200" />
                      ) : (
                        <div className="h-[70px] flex-1 rounded-lg border border-slate-200 bg-slate-50" />
                      )}
                      <button
                        type="button"
                        onClick={refreshCaptcha}
                        className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"
                        title="Refresh"
                      >
                        <RefreshCw className="h-4 w-4" />
                      </button>
                    </div>
                    <Input
                      value={captchaAns}
                      onChange={(e) => setCaptchaAns(e.target.value.toUpperCase())}
                      placeholder="Type the characters"
                      className="mt-2 text-center tracking-[0.3em]"
                    />
                  </>
                )}
              </div>
              <Button className="w-full" disabled={busy || captchaLockedSec > 0 || !username || !password || !captchaAns || !captchaId}>
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
                <button type="button" onClick={backToCredentials} className="text-slate-500 hover:text-slate-700">
                  ← Back
                </button>
                <button type="button" onClick={onResend} className="font-medium text-brand-600 hover:text-brand-700">
                  Resend code
                </button>
              </div>
            </form>
          )}

          {stage === "totp" && (
            <form onSubmit={onTotp} className="space-y-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                <Smartphone className="h-4 w-4 text-brand-600" />
                {totpEnrolling ? "Set up authenticator" : "Authenticator code"}
              </div>
              {totpEnrolling && totpQr && (
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-center">
                  <p className="mb-2 text-xs text-slate-600">
                    Scan with Microsoft Authenticator, Google Authenticator, or Authy
                  </p>
                  <img
                    src={`data:image/png;base64,${totpQr}`}
                    alt="Authenticator QR code"
                    className="mx-auto h-40 w-40 rounded-lg border border-white bg-white p-1"
                  />
                </div>
              )}
              <Input
                value={totp}
                onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))}
                placeholder="6-digit code"
                className="text-center text-lg tracking-[0.5em]"
                autoFocus
              />
              <Button className="w-full" disabled={busy || totp.length !== 6}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Verify
              </Button>
              <button type="button" onClick={backToCredentials} className="text-xs text-slate-500 hover:text-slate-700">
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
