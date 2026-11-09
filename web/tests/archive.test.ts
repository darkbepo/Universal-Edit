import { describe, expect, it } from "vitest";
import { canonicalExt, familyOfExt, sniff } from "../src/core/detect";
import { targetsFor } from "../src/core/registry";
import type { InputFile } from "../src/core/types";
import { safePath } from "../src/engines/archive";

const file = (name: string): InputFile => {
  const ext = canonicalExt(name);
  return { file: new File([], name), name, ext, family: familyOfExt(ext)! };
};

describe("archives", () => {
  it("detects double extensions and aliases", () => {
    expect(canonicalExt("Backup.TAR.GZ")).toBe("tar.gz");
    expect(canonicalExt("x.tgz")).toBe("tar.gz");
    expect(canonicalExt("x.tbz2")).toBe("tar.bz2");
    expect(familyOfExt("rar")).toBe("archive");
    expect(sniff(new Uint8Array([0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c]))).toBe("7z");
  });

  it("never lets a path escape the output folder", () => {
    expect(safePath("a/b.txt")).toBe("a/b.txt");
    expect(safePath("/etc/passwd")).toBe("etc/passwd");
    expect(safePath("..\\..\\win.ini")).toBeNull();
    expect(safePath("a/../../b")).toBeNull();
    expect(safePath("C:/x")).toBeNull();
    expect(safePath("./")).toBeNull();
  });

  it("offers only packing for mixed files, and no 7Z output in the browser", () => {
    expect(targetsFor([file("a.png"), file("b.pdf")]).targets.map((t) => t.id)).toEqual(["pack:zip", "pack:tar.gz"]);
    const ids = targetsFor([file("a.7z")]).targets.map((t) => t.id);
    expect(ids).toEqual(["archive:extract", "zip", "tar", "tar.gz", "tar.xz", "tar.bz2"]);
  });
});
