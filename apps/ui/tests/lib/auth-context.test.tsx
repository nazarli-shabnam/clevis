import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth, type AuthUser } from "@/lib/auth-context";

const TOKEN_KEY = "clevis:token";

function b64url(value: object): string {
  return btoa(JSON.stringify(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function makeJwt(sub: number, email: string): string {
  const header = b64url({ alg: "none", typ: "JWT" });
  const payload = b64url({
    sub: String(sub),
    email,
    name: null,
    is_workspace_admin: false,
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  return `${header}.${payload}.sig`;
}

const passwordUser: AuthUser = {
  id: 2,
  email: "password@example.com",
  name: "Password User",
  is_workspace_admin: false,
};

const cookieUser: AuthUser = {
  id: 1,
  email: "github@example.com",
  name: "GitHub User",
  is_workspace_admin: true,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AuthProvider mount /auth/me race", () => {
  let meDeferred: {
    promise: Promise<Response>;
    resolve: (response: Response) => void;
  };

  beforeEach(() => {
    localStorage.clear();
    meDeferred = (() => {
      let resolve!: (response: Response) => void;
      const promise = new Promise<Response>((res) => {
        resolve = res;
      });
      return { promise, resolve };
    })();

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return meDeferred.promise;
        }
        if (url.endsWith("/auth/login")) {
          return Promise.resolve(
            jsonResponse({
              access_token: makeJwt(passwordUser.id, passwordUser.email),
              user: passwordUser,
            }),
          );
        }
        if (url.endsWith("/auth/logout")) {
          return Promise.resolve(new Response(null, { status: 204 }));
        }
        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("does not overwrite a concurrent password login with a stale /auth/me response", async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(true);
    });

    await act(async () => {
      await result.current.login("password@example.com", "supersecret1234");
    });

    expect(result.current.user).toEqual(passwordUser);
    expect(result.current.token).toBeTruthy();

    await act(async () => {
      meDeferred.resolve(jsonResponse(cookieUser));
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.user).toEqual(passwordUser);
  });

  it("ignores a stale /auth/me response after logout", async () => {
    localStorage.setItem(TOKEN_KEY, makeJwt(cookieUser.id, cookieUser.email));

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      result.current.logout();
    });

    expect(result.current.user).toBeNull();

    await act(async () => {
      meDeferred.resolve(jsonResponse(cookieUser));
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.user).toBeNull();
  });
});

describe("AuthProvider logoutWarning", () => {
  function stubFetch(logoutResponse: () => Promise<Response>) {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return Promise.resolve(new Response(null, { status: 401 }));
        }
        if (url.endsWith("/auth/logout")) {
          return logoutResponse();
        }
        if (url.endsWith("/auth/login")) {
          return Promise.resolve(
            jsonResponse({
              access_token: makeJwt(passwordUser.id, passwordUser.email),
              user: passwordUser,
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
      }),
    );
  }

  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sets logoutWarning when the /auth/logout network call rejects", async () => {
    stubFetch(() => Promise.reject(new Error("network down")));

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      result.current.logout();
    });

    await waitFor(() => {
      expect(result.current.logoutWarning).toBeTruthy();
    });
  });

  it("sets logoutWarning when /auth/logout responds non-ok", async () => {
    stubFetch(() => Promise.resolve(new Response(null, { status: 500 })));

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      result.current.logout();
    });

    await waitFor(() => {
      expect(result.current.logoutWarning).toBeTruthy();
    });
  });

  it("does not set logoutWarning when /auth/logout succeeds", async () => {
    stubFetch(() => Promise.resolve(new Response(null, { status: 204 })));

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      result.current.logout();
    });

    // Give the logout() fetch promise a tick to settle before asserting the negative.
    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.logoutWarning).toBeNull();
  });

  it("clears logoutWarning via clearLogoutWarning", async () => {
    stubFetch(() => Promise.reject(new Error("network down")));

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      result.current.logout();
    });

    await waitFor(() => {
      expect(result.current.logoutWarning).toBeTruthy();
    });

    act(() => {
      result.current.clearLogoutWarning();
    });

    expect(result.current.logoutWarning).toBeNull();
  });

  it("clears logoutWarning on the next successful login", async () => {
    stubFetch(() => Promise.reject(new Error("network down")));

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      result.current.logout();
    });

    await waitFor(() => {
      expect(result.current.logoutWarning).toBeTruthy();
    });

    await act(async () => {
      await result.current.login("password@example.com", "supersecret1234");
    });

    expect(result.current.logoutWarning).toBeNull();
  });
});
