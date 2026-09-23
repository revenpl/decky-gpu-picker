import { ButtonItem, PanelSection, PanelSectionRow, staticClasses } from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { Fragment, useEffect, useState } from "react";
import { FaCopy, FaMicrochip, FaSync } from "react-icons/fa";

interface Gpu {
  name: string;
  pci: string | null;
  source: string;
  index: number;
}

const listGpus = callable<[], Gpu[]>("list_gpus");
const buildCommand = callable<[name: string, index: number | null], string>("build_command");

// Copy via the synchronous execCommand API first (works inside the Steam CEF
// webview, where the async navigator.clipboard API is typically blocked by the
// non-secure context), then fall back to the Clipboard API. Same order as the
// Framegen plugin, which is the reference that works on this device.
async function copyToClipboard(text: string): Promise<boolean> {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.left = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    if (ok) return true;
  } catch (e) {
    console.warn("execCommand copy failed:", e);
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    console.warn("clipboard.writeText failed:", err);
    return false;
  }
}

function Content() {
  const [gpus, setGpus] = useState<Gpu[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setGpus(await listGpus());
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  // Group GPUs by name, preserving first-appearance order. A unique name is
  // clickable itself; a duplicated name lists one clickable row per index
  // (the user picks the exact card deliberately).
  const groups: { name: string; devices: Gpu[] }[] = [];
  for (const gpu of gpus ?? []) {
    const existing = groups.find((g) => g.name === gpu.name);
    if (existing) existing.devices.push(gpu);
    else groups.push({ name: gpu.name, devices: [gpu] });
  }

  const copyFor = async (gpu: Gpu) => {
    try {
      const cmd = await buildCommand(gpu.name, gpu.index);
      if (await copyToClipboard(cmd)) {
        toaster.toast({ title: "Copied command to clipboard", body: cmd });
      } else {
        setManual(cmd);
        toaster.toast({
          title: "No clipboard access",
          body: "Copy the command manually (field below).",
        });
      }
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <PanelSection title="GPU Picker">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => refresh()}>
          <FaSync /> {gpus ? "Refresh GPU list" : "Fetching GPU list…"}
        </ButtonItem>
      </PanelSectionRow>
      {error !== null && (
        <PanelSectionRow>
          <div style={{ color: "red", whiteSpace: "pre-wrap" }}>Error: {error}</div>
        </PanelSectionRow>
      )}
      {groups.map((group, gi) => (
        <Fragment key={gi}>
          {group.devices.length === 1 ? (
            // Unique name: the name row itself is clickable.
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => copyFor(group.devices[0])}>
                <FaMicrochip /> {group.name}
                {group.devices[0].pci !== null && (
                  <span style={{ opacity: 0.6 }}> ({group.devices[0].pci})</span>
                )}
              </ButtonItem>
            </PanelSectionRow>
          ) : (
            <>
              {/* Duplicated name: the header is not clickable; pick an index below. */}
              <PanelSectionRow>
                <div>
                  <FaMicrochip /> {group.name}
                  <span style={{ opacity: 0.6 }}>
                    {" "}({group.devices.length}x - pick an index)
                  </span>
                </div>
              </PanelSectionRow>
              {group.devices.map((gpu) => (
                <PanelSectionRow key={gpu.index}>
                  <ButtonItem layout="below" onClick={() => copyFor(gpu)}>
                    <FaCopy /> #{gpu.index}
                    {gpu.pci !== null && <span style={{ opacity: 0.6 }}> PCI {gpu.pci}</span>}
                    <span style={{ opacity: 0.6 }}> ({gpu.source})</span>
                  </ButtonItem>
                </PanelSectionRow>
              ))}
            </>
          )}
        </Fragment>
      ))}
      {manual !== null && (
        <PanelSectionRow>
          <textarea
            readOnly
            value={manual}
            onFocus={(e) => e.target.select()}
            style={{
              width: "100%",
              boxSizing: "border-box",
              background: "#111",
              color: "#fff",
              padding: 8,
            }}
          />
          <div style={{ fontSize: "small", opacity: 0.7 }}>
            Click the field, Ctrl+C, paste into the game's "Launch Options".
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => {
  return {
    name: "GPU Picker",
    titleView: <div className={staticClasses.Title}>GPU Picker</div>,
    content: <Content />,
    icon: <FaMicrochip />,
  };
});
