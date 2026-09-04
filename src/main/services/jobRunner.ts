import { spawn } from "node:child_process";
import type { CommandPlan } from "../../shared/types.js";

export interface RunCommandResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

export interface RunCommandInput {
  plan: CommandPlan;
  timeoutMs?: number;
  /** 取消信号：触发时杀掉子进程并拒绝，用于用户取消整批转换。 */
  signal?: AbortSignal;
}

export async function runCommand(input: RunCommandInput): Promise<RunCommandResult> {
  const { plan } = input;
  const timeoutMs = input.timeoutMs ?? 120000;
  const signal = input.signal;

  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error("转换已取消"));
      return;
    }

    const child = spawn(plan.executable, plan.args, {
      cwd: plan.cwd,
      env: plan.env ? { ...process.env, ...plan.env } : process.env,
      windowsHide: true
    });

    let stdout = "";
    let stderr = "";

    const cleanup = () => {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", onAbort);
    };

    const timer = setTimeout(() => {
      cleanup();
      child.kill();
      reject(new Error("转换超时"));
    }, timeoutMs);

    const onAbort = () => {
      cleanup();
      child.kill();
      reject(new Error("转换已取消"));
    };

    if (signal) signal.addEventListener("abort", onAbort, { once: true });

    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });

    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });

    child.on("error", (error) => {
      cleanup();
      reject(error);
    });

    child.on("close", (exitCode) => {
      cleanup();
      resolve({
        exitCode: exitCode ?? 1,
        stdout,
        stderr
      });
    });
  });
}
