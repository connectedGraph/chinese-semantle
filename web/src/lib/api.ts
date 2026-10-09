import type { CreateRaceBody } from "./normalize";

function defaultBase(): string {
  const env = import.meta.env.VITE_API_BASE as string | undefined;
  if (env !== undefined) return env;
  // dev：走 Vite 代理（同源）
  if (import.meta.env.DEV) return "";
  // 构建后静态部署（如 :5174）：直接连同主机的后端 :8001
  return `${location.protocol}//${location.hostname}:8001`;
}

const API = defaultBase();

/** 拼出带 base 的 URL（EventSource 等场景用） */
export function apiUrl(path: string): string {
  return API + path;
}

export interface CreateRaceResult {
  race_id: string;
  target_length: number;
  solo: boolean;
}

async function readError(resp: Response): Promise<string> {
  const txt = await resp.text();
  try {
    const j = JSON.parse(txt) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((x) => (x && typeof x === "object" && "msg" in x ? String((x as { msg: unknown }).msg) : JSON.stringify(x)))
        .join("；");
    }
    return txt;
  } catch {
    return txt;
  }
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as T;
}

export function createRace(body: CreateRaceBody): Promise<CreateRaceResult> {
  return postJson<CreateRaceResult>("/api/agent/race", body);
}

export function startRace(raceId: string): Promise<unknown> {
  return postJson(`/api/agent/race/${raceId}/start`);
}

export function sendGuess(raceId: string, word: string): Promise<unknown> {
  return postJson(`/api/agent/race/${raceId}/guess`, { word });
}

export function giveupRace(raceId: string): Promise<unknown> {
  return postJson(`/api/agent/race/${raceId}/giveup`);
}
