import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Loader2,
  RefreshCw,
  ShieldCheck,
  KeyRound,
  Smartphone,
  ArrowLeft,
} from "lucide-react";
import {
  authLogin,
  authResendOtp,
  authVerifyCaptcha,
  authVerifyOtp,
  authVerifyTotp,
  captchaUrl,
  type LoginResult,
} from "../api/client";
import { useAuth } from "../lib/auth";
import { Button, Input } from "../components/ui";
import { useToast } from "../components/toast";
import { cn } from "../lib/utils";

// Sign-in = username + password, then exactly ONE challenge — never stacked.
type Stage = "credentials" | "captcha" | "otp" | "totp";

const STAGE_LABEL: Record<Stage, string> = {
  credentials: "Sign in",
  captcha: "Security check",
  otp: "Email verification",
  totp: "Authenticator",
};

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
  const [totpQr, setTotpQr] = useState<string | null>(null);
  const [totpEnrolling, setTotpEnrolling] = useState(false);

  const [captchaId, setCaptchaId] = useState("");
  const [captchaImg, setCaptchaImg] = useState("");
  const [captchaAns, setCaptchaAns] = useState("");
  const [captchaTick, setCaptchaTick] = useState(0);

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
    } else if (res.status === "captcha_required") {
      setLoginToken(res.login_token!);
      setStage("captcha");
      await refreshCaptcha();
    } else if (res.status === "otp_required") {
      setLoginToken(res.login_token!);
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
      const res = await authLogin(username.trim(), password);
      await handleLoginResult(res);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Login failed");
    } finally {
      setBusy(false);
    }
  };

  const onCaptcha = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res = await authVerifyCaptcha(loginToken, captchaId, captchaAns.trim());
      finish(res.access_token, res.user);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Verification failed");
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
      await authResendOtp(loginToken);
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
    setCaptchaId("");
    setCaptchaImg("");
    setCaptchaAns("");
    setErr(null);
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-canvas px-5 py-10 sm:px-8">
      {/* Single, uniform background across the whole page — soft accents only, never a hard two-tone split. */}
      <div className="login-orb -left-24 -top-24 h-[420px] w-[420px] bg-brand-400/15" aria-hidden />
      <div className="login-orb -right-24 bottom-[-80px] h-[360px] w-[360px] bg-emerald-300/15" aria-hidden />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "conic-gradient(from 180deg at 50% 35%, rgba(13,148,136,0.12), rgba(20,184,166,0.06), rgba(67,78,96,0.10), rgba(13,148,136,0.12))",
          maskImage: "radial-gradient(60% 60% at 50% 40%, rgba(0,0,0,1) 0%, rgba(0,0,0,0) 75%)",
        }}
        aria-hidden
      />
      <div className="pointer-events-none absolute inset-0 bg-grid opacity-30" aria-hidden />

      <div className="relative z-10 flex w-full max-w-6xl items-center justify-center gap-14 xl:gap-20">
        {/* Left tagline — desktop only, flanks the centered card */}
        <div className="hidden w-64 shrink-0 animate-fade-up xl:block">
          <div className="h-0.5 w-10 bg-brand-500" />
          <p className="mt-5 font-serif text-[1.7rem] italic leading-tight text-slate-800">
            "AI-powered <span className="text-brand-600">extraction</span>"
          </p>
          <p className="mt-4 text-[15px] italic leading-relaxed text-slate-500">
            Pull timesheets straight from inbox threads, automatically.
          </p>
        </div>

        {/* Card column */}
        <div className="w-full max-w-[420px] animate-fade-up">
          <div className="login-card p-7 sm:p-8">
            <div className="mb-6 flex justify-center">
              <div className="flex h-32 w-32 items-center justify-center overflow-hidden rounded-2xl shadow-card ring-4 ring-brand-100/60">
                <img src="/timesheets_logo.jpg" alt="Alpha Data" className="h-full w-full object-cover" />
              </div>
            </div>
            {/* Stage header */}
            <div className="mb-6">
              <div className="mb-4 flex items-center gap-2">
                <div
                  className={cn(
                    "h-1 flex-1 rounded-full transition-colors duration-300",
                    stage === "credentials" ? "bg-brand-500" : "bg-brand-300"
                  )}
                />
                <div
                  className={cn(
                    "h-1 flex-1 rounded-full transition-colors duration-300",
                    stage === "credentials" ? "bg-slate-200" : "bg-brand-500"
                  )}
                />
              </div>
              <h2 className="font-serif text-xl font-semibold text-slate-900">{STAGE_LABEL[stage]}</h2>
              <p className="mt-1 text-sm text-slate-500">
                {stage === "credentials" && "Enter your administrator-issued credentials."}
                {stage === "captcha" && "Complete the security check to continue."}
                {stage === "otp" && "We sent a one-time code to your email."}
                {stage === "totp" &&
                  (totpEnrolling
                    ? "Scan the QR code, then enter your first 6-digit code."
                    : "Enter the 6-digit code from your authenticator app.")}
              </p>
            </div>

            {err && (
              <div className="mb-5 flex items-start gap-2.5 rounded-xl border border-rose-200/80 bg-rose-50 px-3.5 py-3 text-sm text-rose-700">
                <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
                {err}
              </div>
            )}

            <div key={stage} className="login-stage-enter">
              {stage === "credentials" && (
                <form onSubmit={onCredentials} className="space-y-4">
                  <div>
                    <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Username
                    </label>
                    <Input
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      autoFocus
                      placeholder="admin"
                      className="h-11"
                    />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Password
                    </label>
                    <Input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="h-11"
                    />
                  </div>
                  <Button className="mt-2 h-11 w-full text-[15px]" disabled={busy || !username || !password}>
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
                    Continue
                  </Button>
                </form>
              )}

              {stage === "captcha" && (
                <form onSubmit={onCaptcha} className="space-y-4">
                  <div className="flex items-center gap-2 rounded-lg bg-brand-50 px-3 py-2 text-sm font-medium text-brand-800">
                    <ShieldCheck className="h-4 w-4 shrink-0" />
                    Solve the CAPTCHA below
                  </div>
                  {captchaLockedSec > 0 ? (
                    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-6 text-center">
                      <p className="text-sm font-semibold text-amber-800">Too many requests</p>
                      <p className="mt-2 font-mono text-4xl font-bold tabular-nums text-amber-900">
                        {captchaLockedSec}s
                      </p>
                      <p className="mt-2 text-xs text-amber-700">Wait, then try again.</p>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center gap-2">
                        {captchaImg ? (
                          <img
                            src={captchaImg}
                            alt="captcha"
                            className="h-[76px] flex-1 rounded-xl border border-slate-200 bg-slate-50 object-contain"
                          />
                        ) : (
                          <div className="h-[76px] flex-1 animate-pulse rounded-xl border border-slate-200 bg-slate-100" />
                        )}
                        <button
                          type="button"
                          onClick={refreshCaptcha}
                          className="flex h-11 w-11 items-center justify-center rounded-xl border border-slate-200 text-slate-500 transition-colors hover:border-brand-200 hover:bg-brand-50 hover:text-brand-600"
                          title="Refresh"
                        >
                          <RefreshCw className="h-4 w-4" />
                        </button>
                      </div>
                      <Input
                        value={captchaAns}
                        onChange={(e) => setCaptchaAns(e.target.value.toUpperCase())}
                        placeholder="Type the characters"
                        className="h-11 text-center text-lg tracking-[0.35em]"
                        autoFocus
                      />
                    </>
                  )}
                  <Button
                    className="h-11 w-full"
                    disabled={busy || captchaLockedSec > 0 || !captchaAns || !captchaId}
                  >
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    Verify &amp; sign in
                  </Button>
                  <BackLink onClick={backToCredentials} />
                </form>
              )}

              {stage === "otp" && (
                <form onSubmit={onOtp} className="space-y-4">
                  <div className="flex items-center gap-2 rounded-lg bg-brand-50 px-3 py-2 text-sm font-medium text-brand-800">
                    <ShieldCheck className="h-4 w-4 shrink-0" />
                    Check your inbox for the code
                  </div>
                  <Input
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                    placeholder="000000"
                    className="h-12 text-center text-xl tracking-[0.6em]"
                    autoFocus
                    inputMode="numeric"
                    maxLength={6}
                  />
                  <Button className="h-11 w-full" disabled={busy || otp.length < 4}>
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    Verify &amp; sign in
                  </Button>
                  <div className="flex items-center justify-between">
                    <BackLink onClick={backToCredentials} />
                    <button
                      type="button"
                      onClick={onResend}
                      className="text-xs font-semibold text-brand-600 transition-colors hover:text-brand-700"
                    >
                      Resend code
                    </button>
                  </div>
                </form>
              )}

              {stage === "totp" && (
                <form onSubmit={onTotp} className="space-y-4">
                  <div className="flex items-center gap-2 rounded-lg bg-brand-50 px-3 py-2 text-sm font-medium text-brand-800">
                    <Smartphone className="h-4 w-4 shrink-0" />
                    {totpEnrolling ? "Set up your authenticator" : "Open your authenticator app"}
                  </div>
                  {totpEnrolling && totpQr && (
                    <div className="rounded-xl border border-slate-200 bg-gradient-to-b from-slate-50 to-white p-4 text-center">
                      <p className="mb-3 text-xs leading-relaxed text-slate-600">
                        Scan with Microsoft Authenticator, Google Authenticator, or Authy
                      </p>
                      <img
                        src={`data:image/png;base64,${totpQr}`}
                        alt="Authenticator QR code"
                        className="mx-auto h-44 w-44 rounded-xl border-4 border-white bg-white shadow-card"
                      />
                    </div>
                  )}
                  <Input
                    value={totp}
                    onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))}
                    placeholder="000000"
                    className="h-12 text-center text-xl tracking-[0.6em]"
                    autoFocus
                    inputMode="numeric"
                    maxLength={6}
                  />
                  <Button className="h-11 w-full" disabled={busy || totp.length !== 6}>
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    Verify &amp; sign in
                  </Button>
                  <BackLink onClick={backToCredentials} />
                </form>
              )}
            </div>
          </div>
        </div>

        {/* Right tagline — desktop only, mirrors the left */}
        <div className="hidden w-64 shrink-0 animate-fade-up text-right xl:block">
          <div className="ml-auto h-0.5 w-10 bg-brand-500" />
          <p className="mt-5 font-serif text-[1.7rem] italic leading-tight text-slate-800">
            "Secure <span className="text-brand-600">vault filing</span>"
          </p>
          <p className="mt-4 text-[15px] italic leading-relaxed text-slate-500">
            Every timesheet organized by employee, month, and project.
          </p>
        </div>
      </div>
    </div>
  );
}

function BackLink({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 transition-colors hover:text-slate-800"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      Back to sign in
    </button>
  );
}
