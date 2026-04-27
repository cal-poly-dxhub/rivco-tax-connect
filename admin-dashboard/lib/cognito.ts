"use client"

import {
  CognitoUser,
  CognitoUserPool,
  AuthenticationDetails,
  CognitoUserSession,
} from "amazon-cognito-identity-js"

declare global {
  interface Window {
    __APP_CONFIG__: {
      API_URL: string
      USER_POOL_ID: string
      USER_POOL_CLIENT_ID: string
    }
  }
}

export function config() {
  if (typeof window === "undefined") {
    return { API_URL: "", USER_POOL_ID: "", USER_POOL_CLIENT_ID: "" }
  }
  return window.__APP_CONFIG__ || { API_URL: "", USER_POOL_ID: "", USER_POOL_CLIENT_ID: "" }
}

function pool() {
  const { USER_POOL_ID, USER_POOL_CLIENT_ID } = config()
  return new CognitoUserPool({ UserPoolId: USER_POOL_ID, ClientId: USER_POOL_CLIENT_ID })
}

export type SignInResult =
  | { kind: "success"; idToken: string; groups: string[] }
  | { kind: "new-password"; user: CognitoUser }
  | { kind: "reset-required"; username: string }
  | { kind: "error"; message: string }

export function signIn(username: string, password: string): Promise<SignInResult> {
  const user = new CognitoUser({ Username: username, Pool: pool() })
  const details = new AuthenticationDetails({ Username: username, Password: password })
  return new Promise((resolve) => {
    user.authenticateUser(details, {
      onSuccess: (session) => resolve({ kind: "success", ...unpackSession(session) }),
      onFailure: (err) => {
        if (err?.code === "PasswordResetRequiredException") {
          resolve({ kind: "reset-required", username })
        } else {
          resolve({ kind: "error", message: err.message || String(err) })
        }
      },
      newPasswordRequired: () => resolve({ kind: "new-password", user }),
    })
  })
}

export function sendResetCode(username: string): Promise<{ ok: boolean; message?: string }> {
  const user = new CognitoUser({ Username: username, Pool: pool() })
  return new Promise((resolve) => {
    user.forgotPassword({
      onSuccess: () => resolve({ ok: true }),
      onFailure: (err) => resolve({ ok: false, message: err.message || String(err) }),
    })
  })
}

export function confirmReset(
  username: string,
  code: string,
  newPassword: string,
): Promise<{ ok: boolean; message?: string }> {
  const user = new CognitoUser({ Username: username, Pool: pool() })
  return new Promise((resolve) => {
    user.confirmPassword(code, newPassword, {
      onSuccess: () => resolve({ ok: true }),
      onFailure: (err) => resolve({ ok: false, message: err.message || String(err) }),
    })
  })
}

export function completeNewPassword(user: CognitoUser, newPassword: string): Promise<SignInResult> {
  return new Promise((resolve) => {
    user.completeNewPasswordChallenge(
      newPassword,
      {},
      {
        onSuccess: (session) => resolve({ kind: "success", ...unpackSession(session) }),
        onFailure: (err) => resolve({ kind: "error", message: err.message || String(err) }),
      }
    )
  })
}

function unpackSession(session: CognitoUserSession): { idToken: string; groups: string[] } {
  const idToken = session.getIdToken().getJwtToken()
  const payload = session.getIdToken().decodePayload() as { "cognito:groups"?: string[] }
  return { idToken, groups: payload["cognito:groups"] || [] }
}

export function currentSession(): Promise<SignInResult | null> {
  return new Promise((resolve) => {
    const user = pool().getCurrentUser()
    if (!user) return resolve(null)
    user.getSession((err: unknown, session: CognitoUserSession) => {
      if (err || !session || !session.isValid()) return resolve(null)
      resolve({ kind: "success", ...unpackSession(session) })
    })
  })
}

export function signOut() {
  pool().getCurrentUser()?.signOut()
}
