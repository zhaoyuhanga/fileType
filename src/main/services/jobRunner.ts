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
}

export async function runCommand(input: RunCommandInput): Promise<RunCommandResult> {
  const { plan } = input;
  const timeoutMs = input.timeoutMs ?? 120000;

  return new Promise((resolve, reject) => {
    const child = spawn(plan.executable, plan.args, {
      cwd: plan.cwd,
      env: plan.env ? { ...process.env, ...plan.env } : process.env,
      windowsHide: true
    });

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("转换超时"));
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });

    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });

    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });

    child.on("close", (exitCode) => {
      clearTimeout(timer);
      resolve({
        exitCode: exitCode ?? 1,
        stdout,
        stderr
      });
    });
  });
}
