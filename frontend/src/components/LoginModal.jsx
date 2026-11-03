import { useState } from 'react';
import { useAuth } from '../auth';

export default function LoginModal({ onClose, expired = false }) {
    const { login } = useAuth();
    const [mode, setMode] = useState('login');          // 'login' | 'register'
    const [email, setEmail] = useState('');
    const [firstName, setFirstName] = useState('');
    const [lastName, setLastName] = useState('');
    const [password, setPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [registered, setRegistered] = useState(false); // show success state

    function switchMode(m) {
        setMode(m);
        setError('');
        setFirstName('');
        setLastName('');
        setPassword('');
        setConfirmPassword('');
    }

    async function handleLogin(e) {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await login(email, password);
            onClose();
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }

    async function handleRegister(e) {
        e.preventDefault();
        setError('');
        if (password !== confirmPassword) {
            setError('Passwords do not match.');
            return;
        }
        if (password.length < 8) {
            setError('Password must be at least 8 characters.');
            return;
        }
        if (!firstName.trim() || !lastName.trim()) {
            setError('Please enter your first and last name.');
            return;
        }
        setLoading(true);
        try {
            const resp = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password, first_name: firstName.trim(), last_name: lastName.trim() }),
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Registration failed.');
            setRegistered(true);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="bg-slate-900 border border-white/20 rounded-xl shadow-2xl p-8 w-full max-w-sm mx-4">

                {/* Header */}
                <div className="flex items-center justify-between mb-5">
                    <h2 className="text-xl font-semibold text-white">
                        {registered ? 'Check your email' : mode === 'login' ? 'Sign in' : 'Create account'}
                    </h2>
                    <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors">
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* Registration success */}
                {registered ? (
                    <div className="space-y-4">
                        <p className="text-slate-300 text-sm leading-relaxed">
                            We sent a confirmation link to <strong className="text-white">{email}</strong>.
                            Click the link in that email to activate your account, then sign in.
                        </p>
                        <button
                            onClick={() => { setRegistered(false); switchMode('login'); }}
                            className="w-full bg-teal-600 hover:bg-teal-500 text-white font-medium py-2 rounded-lg transition-colors text-sm"
                        >
                            Go to sign in
                        </button>
                    </div>
                ) : (
                    <>
                        {/* Mode tabs */}
                        {!expired && (
                            <div className="flex mb-5 border border-white/10 rounded-lg overflow-hidden">
                                {['login', 'register'].map(m => (
                                    <button
                                        key={m}
                                        onClick={() => switchMode(m)}
                                        className={`flex-1 py-1.5 text-sm font-medium transition-colors ${
                                            mode === m
                                                ? 'bg-teal-600 text-white'
                                                : 'text-slate-400 hover:text-white'
                                        }`}
                                    >
                                        {m === 'login' ? 'Sign in' : 'Register'}
                                    </button>
                                ))}
                            </div>
                        )}

                        {expired && (
                            <p className="text-amber-400 text-sm mb-4">Your session has expired. Please sign in again.</p>
                        )}

                        <form onSubmit={mode === 'login' ? handleLogin : handleRegister} className="space-y-4">
                            {mode === 'register' && (
                                <div className="flex gap-3">
                                    <div className="flex-1">
                                        <label className="block text-sm text-slate-300 mb-1">First name</label>
                                        <input
                                            type="text"
                                            required
                                            value={firstName}
                                            onChange={e => setFirstName(e.target.value)}
                                            className="w-full bg-slate-800 border border-white/10 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-teal-500 text-sm"
                                            placeholder="Jane"
                                        />
                                    </div>
                                    <div className="flex-1">
                                        <label className="block text-sm text-slate-300 mb-1">Last name</label>
                                        <input
                                            type="text"
                                            required
                                            value={lastName}
                                            onChange={e => setLastName(e.target.value)}
                                            className="w-full bg-slate-800 border border-white/10 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-teal-500 text-sm"
                                            placeholder="Smith"
                                        />
                                    </div>
                                </div>
                            )}
                            <div>
                                <label className="block text-sm text-slate-300 mb-1">Email</label>
                                <input
                                    type="email"
                                    required
                                    value={email}
                                    onChange={e => setEmail(e.target.value)}
                                    className="w-full bg-slate-800 border border-white/10 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-teal-500 text-sm"
                                    placeholder="you@example.com"
                                    autoFocus
                                />
                            </div>
                            <div>
                                <label className="block text-sm text-slate-300 mb-1">Password</label>
                                <input
                                    type="password"
                                    required
                                    value={password}
                                    onChange={e => setPassword(e.target.value)}
                                    className="w-full bg-slate-800 border border-white/10 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-teal-500 text-sm"
                                    placeholder="••••••••"
                                />
                            </div>
                            {mode === 'register' && (
                                <div>
                                    <label className="block text-sm text-slate-300 mb-1">Confirm password</label>
                                    <input
                                        type="password"
                                        required
                                        value={confirmPassword}
                                        onChange={e => setConfirmPassword(e.target.value)}
                                        className="w-full bg-slate-800 border border-white/10 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-teal-500 text-sm"
                                        placeholder="••••••••"
                                    />
                                </div>
                            )}

                            {error && <p className="text-red-400 text-sm">{error}</p>}

                            <button
                                type="submit"
                                disabled={loading}
                                className="w-full bg-teal-600 hover:bg-teal-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium py-2 rounded-lg transition-colors text-sm"
                            >
                                {loading
                                    ? (mode === 'login' ? 'Signing in…' : 'Registering…')
                                    : (mode === 'login' ? 'Sign in' : 'Create account')}
                            </button>
                        </form>
                    </>
                )}
            </div>
        </div>
    );
}
