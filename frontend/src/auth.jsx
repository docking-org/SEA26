import { createContext, useContext, useState, useCallback } from 'react';

const AuthContext = createContext(null);

const TOKEN_KEY = 'sea_access_token';
const EMAIL_KEY = 'sea_user_email';
const FIRST_NAME_KEY = 'sea_first_name';

export function AuthProvider({ children }) {
    const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || null);
    const [userEmail, setUserEmail] = useState(() => localStorage.getItem(EMAIL_KEY) || null);
    const [firstName, setFirstName] = useState(() => localStorage.getItem(FIRST_NAME_KEY) || null);
    const [showLoginModal, setShowLoginModal] = useState(false);
    const [sessionExpiredFlag, setSessionExpiredFlag] = useState(false);

    const login = useCallback(async (email, password) => {
        const resp = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || 'Login failed');
        }
        localStorage.setItem(TOKEN_KEY, data.access_token);
        localStorage.setItem(EMAIL_KEY, email);
        localStorage.setItem(FIRST_NAME_KEY, data.first_name || '');
        setToken(data.access_token);
        setUserEmail(email);
        setFirstName(data.first_name || null);
        setShowLoginModal(false);
        setSessionExpiredFlag(false);
        return data;
    }, []);

    const logout = useCallback(() => {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(EMAIL_KEY);
        localStorage.removeItem(FIRST_NAME_KEY);
        setToken(null);
        setUserEmail(null);
        setFirstName(null);
    }, []);

    const sessionExpired = useCallback(() => {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(EMAIL_KEY);
        localStorage.removeItem(FIRST_NAME_KEY);
        setToken(null);
        setUserEmail(null);
        setFirstName(null);
        setSessionExpiredFlag(true);
        setShowLoginModal(true);
    }, []);

    const closeLoginModal = useCallback(() => {
        setShowLoginModal(false);
        setSessionExpiredFlag(false);
    }, []);

    return (
        <AuthContext.Provider value={{ token, userEmail, firstName, login, logout, sessionExpired, showLoginModal, setShowLoginModal, closeLoginModal, sessionExpiredFlag }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    return useContext(AuthContext);
}
