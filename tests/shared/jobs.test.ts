import { describe, expect, it } from "vitest";
import { cancelJob, createJobsForSelection, failJob, startJob, succeedJob } from "../../src/shared/jobs";

describe("jobs", () => {
  it("creates jobs for selected files", () => {
    const jobs = createJobsForSelection(
      [
        {
          id: "a",
          path: "C:/a.txt",
          name: "a.txt",
          extension: "txt",
          format: "txt",
          category: "document",
          sizeBytes: 1,
          selected: true,
          status: "queued",
          progress: 0
        },
        {
          id: "b",
          path: "C:/b.txt",
          name: "b.txt",
          extension: "txt",
          format: "txt",
          category: "document",
          sizeBytes: 1,
          selected: false,
          status: "queued",
          progress: 0
        }
      ],
      {
        id: "txt-to-pdf",
        label: "TXT 转 PDF",
        sourceFormats: ["txt"],
        targetFormat: "pdf",
        category: "document",
        engine: "pandoc"
      }
    );

    expect(jobs).toHaveLength(1);
    expect(jobs[0].status).toBe("queued");
  });

  it("transitions jobs", () => {
    const job = {
      id: "a",
      fileId: "a",
      actionId: "txt-to-pdf",
      status: "queued" as const,
      progress: 0
    };

    expect(startJob(job).status).toBe("running");
    expect(succeedJob(job).status).toBe("succeeded");
    expect(failJob(job, "nope").status).toBe("failed");
    expect(cancelJob(job).status).toBe("cancelled");
  });
});
