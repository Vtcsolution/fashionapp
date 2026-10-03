"use client";
/* eslint-disable @next/next/no-img-element */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CATEGORY_LABEL, groupByCategory, keyOf, removeCategory, toggleSelect, tryOnQueue, tryOnReadiness,
  type CreditInfo, type Selection,
} from "../lib/selection";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Product = {
  retailer: string;
  product_id: string;
  name: string;
  price: string | null;
  currency: string | null;
  url: string;
  affiliate_url: string | null;
  image_url: string;
  image_urls: string[];
  category: string;
};
type Verification = { overall: string; checks: Record<string, string>; notes: string; performed_by: string[] };
type Step = {
  id: number;
  step: number;
  // PLANNED | SENT | GENERATED | VERIFIED (OpenAI+Gemini passed) | REVIEW_REQUIRED | REJECTED | FAILED | SKIPPED
  status: string;
  provider: string;
  simulated: boolean; // true = mock result: never analysed, never "applied"
  fashn_prediction_id: string | null;
  error: string | null;
  person_url: string;
  product_presence: string; // NOT_GENERATED | REVIEW_REQUIRED | PRESENT | ABSENT
  product: {
    id: number; retailer_product_id: string; category: string; name: string; price: string | null; currency: string | null;
    retailer: string; url: string; affiliate_url: string | null; image_url: string;
  };
  result: null | { url: string; verification: Verification };
};
type FinalVerification = {
  overall: string; identity_preserved: string; notes: string; performed_by: string[];
  items: { index: number; name: string; category: string; present: string; matches: string }[];
};
type Run = {
  id: number; status: string; total_steps: number; person_url: string; steps: Step[]; final_url: string | null;
  summary: string; simulated: boolean; final_status: string; final_verification: FinalVerification | null; credits_required: number;
};

const CHECK_LABELS: Record<string, string> = {
  product_presence: "Product presence",
  product_correspondence: "Product correspondence",
  color: "Color",
  details: "Distinctive details",
  earlier_items_preserved: "Earlier items preserved",
  placement: "Placement",
  identity_preserved: "Identity preserved",
};
const tone = (v: string) =>
  ["PASS", "PRESENT", "VERIFIED"].includes(v) ? "bg-emerald-100 text-emerald-800"
    : ["FAIL", "FAILED", "REJECTED", "ABSENT"].includes(v) ? "bg-red-100 text-red-800"
    : ["PLANNED", "SKIPPED", "NOT_GENERATED", "IN_PROGRESS", "STOPPED", "NOT_VERIFIED", "SIMULATED"].includes(v) ? "bg-gray-100 text-gray-600"
    : "bg-amber-100 text-amber-800";
const statusBox = (v: string) =>
  v === "VERIFIED" ? "border-emerald-500 bg-emerald-50 text-emerald-900"
    : v === "FAILED" || v === "STOPPED" ? "border-red-500 bg-red-50 text-red-800"
    : "border-amber-500 bg-amber-50 text-amber-900";
// What we are allowed to say about the FINAL image. "Verified" only when the whole-look check passed.
const finalHeadline = (v: string) =>
  ({ VERIFIED: "VERIFIED — every selected product confirmed in the final image",
     REVIEW_REQUIRED: "REVIEW REQUIRED — not confirmed by verification",
     FAILED: "FAILED — a selected product is missing, wrong or the person changed",
     STOPPED: "STOPPED — a step failed or was rejected", IN_PROGRESS: "In progress…" } as Record<string, string>)[v] ?? v;
// A step is "VERIFIED" only when OpenAI + Gemini analysed it. Mock results are never analysed.
const stepBadge = (s: { simulated: boolean; status: string }) => (s.simulated && s.status !== "PLANNED" ? "NOT_VERIFIED" : s.status);
const label = (v: string) => v.replace(/_/g, " ");
const shopUrl = (p: { url: string; affiliate_url: string | null }) => p.affiliate_url || p.url;
const price = (p: { price: string | null; currency: string | null }) => (p.price ? `${p.price} ${p.currency ?? ""}` : "Price on site");
const retailerName = (r: string) => (r === "aliexpress" ? "AliExpress" : r === "ebay" ? "eBay" : r);

function StepTitle({ n, title, hint }: { n: number; title: string; hint?: string }) {
  return (
    <div className="mb-3 flex items-baseline gap-3">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-black text-sm font-bold text-white">{n}</span>
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      {hint && <span className="text-sm text-gray-500">{hint}</span>}
    </div>
  );
}

export default function Page() {
  const [mode, setMode] = useState("");
  const [credits, setCredits] = useState<CreditInfo | null>(null);
  const [personPath, setPersonPath] = useState<string | null>(null);
  const [personUrl, setPersonUrl] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [searched, setSearched] = useState<string[]>([]);
  const [parser, setParser] = useState("");
  const [retailerStatus, setRetailerStatus] = useState<Record<string, string>>({});
  const [hasSearched, setHasSearched] = useState(false);
  const [selection, setSelection] = useState<Selection<Product>>({});
  const [run, setRun] = useState<Run | null>(null);
  const [progress, setProgress] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  const refreshHealth = () =>
    fetch(`${API}/api/health`)
      .then((r) => r.json())
      .then((j) => {
        setMode(j.vto_mode);
        setCredits(j.credits ?? null);
      })
      .catch(() => setMode("offline"));
  useEffect(() => {
    refreshHealth();
  }, []);

  const groups = useMemo(() => groupByCategory(products), [products]);
  const queue = tryOnQueue(selection);
  const live = mode === "live";
  const readiness = tryOnReadiness(!!personPath, queue, live ? credits : null);
  const cost = credits ? queue.length * credits.per_generation : 0;

  async function call<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(`${API}${path}`, init);
    if (!r.ok) {
      const d = (await r.json().catch(() => ({}))).detail;
      throw new Error(typeof d === "string" ? d : Array.isArray(d) ? (d[0]?.msg ?? "Invalid request") : `HTTP ${r.status}`);
    }
    return r.json();
  }
  const guard = async (text: string, fn: () => Promise<void>) => {
    setError("");
    setBusy(text);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
      setProgress("");
    }
  };

  const upload = (f: File) =>
    guard("Uploading your photo…", async () => {
      const fd = new FormData();
      fd.append("file", f);
      const j = await call<{ person_path: string; url: string }>("/api/uploads/person", { method: "POST", body: fd });
      setPersonPath(j.person_path);
      setPersonUrl(`${API}${j.url}`);
      setRun(null);
    });
  const removePhoto = () => {
    setPersonPath(null);
    setPersonUrl(null);
    setRun(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const search = () =>
    guard("Searching eBay and AliExpress…", async () => {
      const j = await call<{ products: Product[]; retailers: Record<string, string>; searched: string[]; parser: string }>(
        `/api/products/search?q=${encodeURIComponent(prompt)}`,
      );
      setProducts(j.products);
      setRetailerStatus(j.retailers);
      setSearched(j.searched);
      setParser(j.parser);
      setHasSearched(true);
      setSelection({});
      setRun(null);
    });

  // 1) store the whole plan on the server, 2) execute it one step per request. Each step is one generation
  // that starts from the raw result of the previous step. The first failure stops the run. No retries.
  const tryOn = () => {
    if (!readiness.ok || !personPath) return;
    if (live && !window.confirm(`This runs ${queue.length} REAL FASHN generation${queue.length === 1 ? "" : "s"} (${cost} credits). Continue?`)) return;
    const items = [...queue];
    return guard(live ? "Creating your try-on…" : "Creating a mock try-on…", async () => {
      setRun(null);
      const ids: number[] = [];
      for (let i = 0; i < items.length; i++) {
        setProgress(`Preparing product ${i + 1} of ${items.length}…`);
        const sel = await call<{ id: number }>("/api/products/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(items[i]),
        });
        ids.push(sel.id);
      }
      let r = await call<Run>("/api/tryon/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ person_path: personPath, product_ids: ids }),
      });
      setRun(r);
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
      while (r.status === "PLANNED" || r.status === "RUNNING") {
        const i = r.steps.findIndex((s) => s.status === "PLANNED");
        if (i < 0) break;
        setProgress(`Step ${i + 1} of ${r.total_steps}: ${r.steps[i].product.name.slice(0, 50)}`);
        r = await call<Run>(`/api/tryon/run/${r.id}/next`, { method: "POST" });
        setRun(r);
      }
      refreshHealth();
    });
  };

  const scrollToCategory = (c: string) => document.getElementById(`cat-${c}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

  const steps = run?.steps ?? [];
  const generated = steps.filter((s) => s.result).length; // a FASHN image exists: says nothing about correctness
  const accepted = steps.filter((s) => ["VERIFIED", "REVIEW_REQUIRED"].includes(s.status) && s.result);
  const lastImage = accepted[accepted.length - 1];
  const failed = steps.find((s) => s.status === "FAILED" || s.status === "REJECTED");

  return (
    <div className="min-h-screen bg-gradient-to-b from-violet-50 via-white to-white text-gray-900">
      <header className="mx-auto max-w-6xl px-6 pt-10 pb-4 text-center">
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Try<span className="text-violet-600">On</span>U
        </h1>
        <p className="mt-2 text-lg text-gray-600">Describe an outfit. Pick real products. See yourself in them.</p>
        {mode && (
          <p className={`mt-3 inline-block rounded-full px-3 py-1 text-xs font-semibold ${live ? "bg-red-100 text-red-800" : mode === "mock" ? "bg-amber-100 text-amber-800" : "bg-gray-200 text-gray-700"}`}>
            {live
              ? `LIVE — real FASHN generations${credits ? ` · ${Math.max(0, credits.cap - credits.spent)} of ${credits.cap} credits left` : ""}`
              : mode === "mock" ? "MOCK / DEMO MODE — simulated images; nothing is generated by FASHN or verified" : "API offline — start the backend"}
          </p>
        )}
      </header>

      <main className="mx-auto max-w-6xl space-y-12 px-6 pb-24">
        {error && <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-800">{error}</div>}
        {busy && <div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-violet-800">⏳ {progress || busy}</div>}

        {/* 1. Upload */}
        <section>
          <StepTitle n={1} title="Upload your photo" hint="A clear, full-body photo works best" />
          {personUrl ? (
            <div className="flex items-center gap-5 rounded-2xl border bg-white p-4 shadow-sm">
              <img src={personUrl} alt="You" className="h-44 w-32 rounded-xl object-cover" />
              <div className="space-y-2">
                <p className="font-medium">Looking good. This photo will be used for your try-on.</p>
                <div className="flex gap-2">
                  <button className="rounded-lg border px-3 py-1.5 text-sm hover:bg-gray-50" onClick={() => fileRef.current?.click()}>Replace</button>
                  <button className="rounded-lg border px-3 py-1.5 text-sm text-red-700 hover:bg-red-50" onClick={removePhoto}>Remove</button>
                </div>
              </div>
            </div>
          ) : (
            <button onClick={() => fileRef.current?.click()} className="flex h-44 w-full flex-col items-center justify-center rounded-2xl border-2 border-dashed border-violet-300 bg-white text-gray-600 transition hover:border-violet-500 hover:bg-violet-50">
              <span className="text-3xl">📷</span>
              <span className="mt-1 font-medium">Click to upload a photo</span>
              <span className="text-xs text-gray-400">JPG, PNG or WebP</span>
            </button>
          )}
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </section>

        {/* 2. Prompt */}
        {personPath && (
          <section>
            <StepTitle n={2} title="What do you want to wear?" />
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                className="flex-1 rounded-xl border bg-white p-4 text-lg shadow-sm outline-none focus:ring-2 focus:ring-violet-400"
                placeholder="e.g. beige jacket, white dress, brown sandals, brown leather handbag"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && prompt.trim() && !busy && search()}
              />
              <button className="rounded-xl bg-violet-600 px-8 py-4 text-lg font-bold text-white shadow hover:bg-violet-700 disabled:opacity-50" disabled={!prompt.trim() || !!busy} onClick={search}>
                SEARCH
              </button>
            </div>
            <p className="mt-2 text-xs text-gray-500">Up to 6 items per search · separate items with commas · add a budget like “under $100”</p>
          </section>
        )}

        {/* 3. Products by category */}
        {personPath && hasSearched && (
          <section className="space-y-6">
            <StepTitle n={3} title="Pick your items" hint="One per category — pick as many categories as you like" />
            <p className="text-xs text-gray-500">
              Understood{parser === "openai" ? " by OpenAI" : ""} as: {searched.map((s) => `“${s}”`).join(", ")} · {Object.entries(retailerStatus).map(([k, v]) => `${retailerName(k)}: ${v}`).join(" · ")}
            </p>
            {groups.length === 0 && <p className="rounded-xl border bg-white p-6 text-gray-600">No products found. Try different words.</p>}
            {groups.map(({ category, items }) => (
              <div key={category} id={`cat-${category}`} className="scroll-mt-6">
                <h3 className="mb-2 text-sm font-bold uppercase tracking-widest text-gray-500">
                  {CATEGORY_LABEL[category] ?? category} <span className="font-normal normal-case text-gray-400">({items.length})</span>
                </h3>
                <div className="flex gap-4 overflow-x-auto pb-2">
                  {items.map((p) => {
                    const on = selection[category] && keyOf(selection[category]) === keyOf(p);
                    return (
                      <div key={keyOf(p)} className={`w-48 shrink-0 rounded-2xl border bg-white p-3 text-sm shadow-sm transition ${on ? "border-violet-600 ring-2 ring-violet-500" : "hover:shadow-md"}`}>
                        <div className="relative">
                          <img src={p.image_url} alt={p.name} loading="lazy" className="h-48 w-full rounded-xl bg-gray-50 object-contain" />
                          {on && <span className="absolute right-2 top-2 rounded-full bg-violet-600 px-2 py-0.5 text-xs font-bold text-white">Selected</span>}
                        </div>
                        <p className="mt-2 line-clamp-2 h-10 font-medium leading-5">{p.name}</p>
                        <p className="mt-1 font-semibold">{price(p)}</p>
                        <p className="text-xs text-gray-500">{retailerName(p.retailer)}</p>
                        <div className="mt-2 flex items-center justify-between">
                          <a href={shopUrl(p)} target="_blank" rel="noreferrer" className="text-xs text-violet-700 underline">View</a>
                          <button onClick={() => setSelection((s) => toggleSelect(s, p))} className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${on ? "bg-violet-600 text-white" : "border hover:bg-gray-50"}`}>
                            {on ? "Remove" : "Select"}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </section>
        )}

        {/* 4. Your look: every selected product, in the order it will be applied */}
        {personPath && queue.length > 0 && (
          <section>
            <StepTitle n={4} title="Your look" hint={`${queue.length} product${queue.length === 1 ? "" : "s"} selected`} />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {queue.map((p, i) => (
                <div key={keyOf(p)} className="flex gap-3 rounded-2xl border bg-white p-3 shadow-sm">
                  <div className="relative">
                    <img src={p.image_url} alt={p.name} className="h-24 w-24 shrink-0 rounded-xl bg-gray-50 object-contain" />
                    <span className="absolute -left-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full bg-violet-600 text-xs font-bold text-white">{i + 1}</span>
                  </div>
                  <div className="min-w-0 flex-1 text-sm">
                    <p className="text-xs font-bold uppercase tracking-widest text-gray-400">{CATEGORY_LABEL[p.category] ?? p.category}</p>
                    <p className="line-clamp-2 font-medium leading-5">{p.name}</p>
                    <p className="font-semibold">{price(p)} <span className="font-normal text-gray-500">· {retailerName(p.retailer)}</span></p>
                    <div className="mt-1 flex gap-3 text-xs">
                      <button className="text-violet-700 underline" onClick={() => scrollToCategory(p.category)}>Change</button>
                      <button className="text-red-700 underline" onClick={() => setSelection((s) => removeCategory(s, p.category))}>Remove</button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* TRYON U + planned sequence */}
        {personPath && hasSearched && (
          <section className="text-center">
            <button
              onClick={tryOn}
              disabled={!readiness.ok || !!busy}
              className="rounded-2xl bg-gradient-to-r from-violet-600 to-fuchsia-600 px-14 py-5 text-2xl font-extrabold tracking-wide text-white shadow-lg transition hover:scale-[1.02] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100"
            >
              TRYON U
            </button>
            {queue.length > 0 && (
              <div className="mx-auto mt-4 max-w-xl rounded-2xl border bg-white p-4 text-left shadow-sm">
                <p className="text-sm font-semibold">
                  {queue.length} product{queue.length === 1 ? "" : "s"} will be applied sequentially:
                </p>
                <ol className="mt-2 space-y-1 text-sm">
                  {queue.map((p, i) => (
                    <li key={keyOf(p)} className="flex gap-2">
                      <span className="w-5 shrink-0 font-bold text-violet-600">{i + 1}.</span>
                      <span className="min-w-0 flex-1 truncate">{p.name}</span>
                      <span className="shrink-0 text-xs uppercase text-gray-400">{CATEGORY_LABEL[p.category] ?? p.category}</span>
                    </li>
                  ))}
                </ol>
                <p className="mt-2 text-xs text-gray-500">
                  Each step starts from the previous result and is checked by OpenAI + Gemini{live ? "" : " (not in mock mode)"}.
                </p>
                <p className="mt-1 text-xs font-semibold text-gray-700">
                  {queue.length} product{queue.length === 1 ? "" : "s"} × {credits?.per_generation ?? 2} credits = {cost || queue.length * 2} FASHN credits{live ? "" : " (a real run would cost this; mock mode costs nothing)"}.
                </p>
              </div>
            )}
            {!readiness.ok && <p className="mx-auto mt-3 max-w-xl rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{readiness.message}</p>}
          </section>
        )}

        {/* Result */}
        {run && (
          <section ref={resultRef} className="scroll-mt-6 space-y-6">
            <StepTitle n={5} title="Your try-on" hint={`${generated} of ${run.total_steps} steps generated`} />

            {run.simulated ? (
              <div className="rounded-2xl border-2 border-red-500 bg-red-50 p-5">
                <p className="text-2xl font-extrabold text-red-700">MOCK / DEMO</p>
                <p className="mt-1 text-red-800">
                  These images are simulated placeholders. Nothing was generated by FASHN and nothing was verified. This is NOT
                  evidence that multi-product try-on works.
                </p>
              </div>
            ) : (
              <div className={`rounded-2xl border-2 p-5 ${statusBox(run.final_status)}`}>
                <p className="text-xs font-bold uppercase tracking-widest opacity-70">Final image status</p>
                <p className="text-2xl font-extrabold">{finalHeadline(run.final_status)}</p>
                <p className="mt-1 text-sm">{run.summary}</p>
              </div>
            )}

            {failed && (
              <p className="rounded-xl border border-red-300 bg-red-50 p-3 text-red-800">
                Step {failed.step} ({failed.product.name.slice(0, 50)}) {failed.status === "REJECTED" ? "was rejected by verification" : "failed"}: {failed.error}. Nothing was retried; later steps were skipped.
                {lastImage ? ` Showing the last accepted image (step ${lastImage.step}).` : ""}
              </p>
            )}

            <div className="grid gap-8 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              <div>
                {run.final_url ? (
                  <img src={`${API}${run.final_url}`} alt={run.simulated ? "Mock image (not a try-on)" : "Final try-on image"} className={`w-full rounded-2xl border shadow-lg ${run.simulated ? "opacity-90 ring-4 ring-red-400" : ""}`} />
                ) : (
                  <div className="flex h-96 items-center justify-center rounded-2xl border bg-white text-gray-400">{busy ? "Generating…" : "No image generated"}</div>
                )}
                <p className="mt-2 text-xs text-gray-500">
                  {run.simulated ? "MOCK / DEMO placeholder — not a try-on." : lastImage ? "Raw FASHN output of the last accepted step, unedited." : ""}
                </p>
              </div>

              <div className="space-y-5">
                <div className="rounded-2xl border bg-white p-4 shadow-sm">
                  <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Final image check (all selected products)</p>
                  {run.final_verification ? (
                    <>
                      <p className={`mt-1 inline-block rounded-lg px-3 py-1 font-bold ${tone(run.final_verification.overall)}`}>{label(run.final_verification.overall)}</p>
                      <p className="mt-1 text-xs text-gray-500">Analysed by: {run.final_verification.performed_by.join(" + ") || "no model"}</p>
                      <ul className="mt-3 space-y-1 text-sm">
                        {run.final_verification.items.map((it) => (
                          <li key={it.index} className="flex items-center justify-between gap-2">
                            <span className="truncate">{it.index}. {it.name}</span>
                            <span className="flex shrink-0 gap-1">
                              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(it.present)}`}>in image: {label(it.present)}</span>
                              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(it.matches)}`}>match: {label(it.matches)}</span>
                            </span>
                          </li>
                        ))}
                        <li className="flex items-center justify-between">
                          <span>Person identity</span>
                          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(run.final_verification.identity_preserved)}`}>{label(run.final_verification.identity_preserved)}</span>
                        </li>
                      </ul>
                      {run.final_verification.notes && <p className="mt-3 text-xs text-gray-600">{run.final_verification.notes}</p>}
                    </>
                  ) : (
                    <p className="mt-2 text-sm text-gray-600">
                      {run.simulated ? "NOT VERIFIED — mock results are never analysed." : run.final_status === "IN_PROGRESS" ? "Runs after the last step." : "No final verification was produced. Manual review required."}
                    </p>
                  )}
                </div>

                <div className="rounded-2xl border bg-white p-4 shadow-sm">
                  <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Selected products</p>
                  <div className="mt-2 space-y-3">
                    {steps.map((s) => (
                      <div key={s.id} className="flex gap-3">
                        <img src={`${API}${s.product.image_url}`} alt={s.product.name} className="h-20 w-20 shrink-0 rounded-xl bg-gray-50 object-contain" />
                        <div className="min-w-0 flex-1 text-sm">
                          <p className="line-clamp-2 font-medium leading-5">{s.step}. {s.product.name}</p>
                          <p className="font-semibold">{price(s.product)} <span className="font-normal text-gray-500">· {retailerName(s.product.retailer)}</span></p>
                          <a href={shopUrl(s.product)} target="_blank" rel="noreferrer" className="mt-1 inline-block rounded-lg bg-black px-4 py-1.5 text-xs font-bold tracking-wide text-white hover:bg-gray-800">
                            SHOP PRODUCT
                          </a>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Per-step tracking: every selected product, start to finish */}
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-widest text-gray-400">Step tracking</p>
              <div className="overflow-x-auto rounded-2xl border bg-white shadow-sm">
                <table className="w-full min-w-[900px] text-left text-sm">
                  <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="p-3">Step</th><th className="p-3">Product</th><th className="p-3">Category</th>
                      <th className="p-3">Retailer</th><th className="p-3">Product ID</th><th className="p-3">FASHN prediction ID</th>
                      <th className="p-3">Result image</th><th className="p-3">Verification</th>
                    </tr>
                  </thead>
                  <tbody>
                    {steps.map((s) => (
                      <tr key={s.id} className="border-t align-top">
                        <td className="p-3 font-bold">{s.step}</td>
                        <td className="p-3">
                          <div className="flex items-center gap-2">
                            <img src={`${API}${s.product.image_url}`} alt="" className="h-10 w-10 rounded bg-gray-50 object-contain" />
                            <span className="line-clamp-2 max-w-[200px]">{s.product.name}</span>
                          </div>
                        </td>
                        <td className="p-3">{CATEGORY_LABEL[s.product.category] ?? s.product.category}</td>
                        <td className="p-3">{retailerName(s.product.retailer)}</td>
                        <td className="p-3 font-mono text-xs text-gray-600">{s.product.retailer_product_id.slice(0, 18)}</td>
                        <td className="p-3 font-mono text-xs text-gray-600">{s.fashn_prediction_id ?? (s.simulated ? "— (mock)" : "—")}</td>
                        <td className="p-3">
                          {s.result ? (
                            <div className="relative h-20 w-14">
                              <img src={`${API}${s.result.url}`} alt={`Step ${s.step} result`} className="h-20 w-14 rounded border object-cover" />
                              {s.simulated && <span className="absolute inset-x-0 bottom-0 bg-red-600 text-center text-[9px] font-bold text-white">MOCK</span>}
                            </div>
                          ) : (
                            <span className="text-xs text-gray-400">no image</span>
                          )}
                        </td>
                        <td className="p-3">
                          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(stepBadge(s))}`}>{label(stepBadge(s))}</span>
                          <p className="mt-1 text-xs text-gray-500">
                            {s.simulated ? "not analysed (mock)" : s.result ? `analysed by: ${s.result.verification.performed_by.join(" + ") || "no model"}` : ""}
                          </p>
                          {!s.simulated && s.result && (
                            <details className="mt-1 text-xs">
                              <summary className="cursor-pointer text-violet-700">checks</summary>
                              <ul className="mt-1 space-y-0.5">
                                {Object.entries(s.result.verification.checks).map(([k, v]) => (
                                  <li key={k} className="flex items-center justify-between gap-2">
                                    <span>{CHECK_LABELS[k] ?? k}</span>
                                    <span className={`rounded px-1.5 py-0.5 font-semibold ${tone(v)}`}>{label(v)}</span>
                                  </li>
                                ))}
                              </ul>
                              {s.result.verification.notes && <p className="mt-1 max-w-[240px] text-gray-600">{s.result.verification.notes.slice(0, 220)}</p>}
                            </details>
                          )}
                          {s.error && s.status !== "SKIPPED" && <p className="mt-1 max-w-[220px] text-xs text-red-700">{s.error.slice(0, 120)}</p>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {steps.length > 1 && (
              <div>
                <p className="mb-2 text-xs font-bold uppercase tracking-widest text-gray-400">Image after each step</p>
                <div className="flex gap-4 overflow-x-auto pb-2">
                  <figure className="w-40 shrink-0">
                    <img src={`${API}${run.person_url}`} alt="Original" className="h-52 w-full rounded-xl border object-cover" />
                    <figcaption className="mt-1 text-xs text-gray-500">Original</figcaption>
                  </figure>
                  {steps.map((s) => (
                    <figure key={s.id} className="w-40 shrink-0">
                      {s.result ? (
                        <div className="relative">
                          <img src={`${API}${s.result.url}`} alt={`Step ${s.step}`} className="h-52 w-full rounded-xl border object-cover" />
                          {s.simulated && <span className="absolute left-1 top-1 rounded bg-red-600 px-1 text-[10px] font-bold text-white">MOCK / DEMO</span>}
                        </div>
                      ) : (
                        <div className={`flex h-52 items-center justify-center rounded-xl border text-xs ${tone(s.status)}`}>{label(s.status).toLowerCase()}</div>
                      )}
                      <figcaption className="mt-1 text-xs text-gray-600">{s.step}. {s.product.name.slice(0, 28)}</figcaption>
                    </figure>
                  ))}
                </div>
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
