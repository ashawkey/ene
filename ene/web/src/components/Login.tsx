import { ArrowRight, KeyRound } from 'lucide-react'
import { useState } from 'react'

import { errorMessage, login } from '../lib/api'
import { Spinner } from './ui'

export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <main className="login-shell">
      <form
        className="login glass"
        onSubmit={async (event) => {
          event.preventDefault()
          if (busy || !token) return
          setBusy(true)
          setError('')
          try {
            await login(token)
            onSuccess()
          } catch (exc) {
            setError(errorMessage(exc, 'Sign-in failed.'))
          } finally {
            setBusy(false)
          }
        }}
      >
        <img src="/favicon.svg" alt="" className="login-logo" />
        <h1>ene</h1>
        <p className="muted">Enter the hub access token.</p>
        <div className="login-field">
          <KeyRound size={16} />
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            value={token}
            placeholder="Token"
            aria-label="Access token"
            onChange={(event) => setToken(event.target.value)}
          />
          <button type="submit" className="icon-btn md accent" aria-label="Sign in" disabled={busy || !token}>
            {busy ? <Spinner /> : <ArrowRight size={18} />}
          </button>
        </div>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
      </form>
    </main>
  )
}
