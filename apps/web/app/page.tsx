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
type Verification = { overall: string; checks: Record<string, string>; notes: string };
type Step = {
  id: number;
  step: number;
  status: string; // PLANNED | SENT | GENERATED | VERIFIED | FAILED | SKIPPED
  provider: string;
  error: string | null;
  person_url: string;
  product_presence: string; // NOT_GENERATED | REVIEW_REQUIRED | PRESENT | ABSENT
  product: {
    id: number; retailer_product_id: string; category: string; name: string; price: string | null; currency: string | null;
    retailer: string; url: string; affiliate_url: string | null; image_url: string;
  };
  result: null | { url: string; verification: Verification };
};
type Run = { id: number; status: string; total_steps: number; person_url: string; steps: Step[]; final_url: string | null; summary: string };

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
  ["PASS", "PRESENT", "VERIFIED", "COMPLETE"].includes(v) ? "bg-emerald-100 text-emerald-800"
    : ["FAIL", "FAILED", "REJECTED", "ABSENT"].includes(v) ? "bg-red-100 text-red-800"
    : ["PLANNED", "SKIPPED", "NOT_GENERATED"].includes(v) ? "bg-gray-100 text-gray-600"
    : "bg-amber-100 text-amber-800";
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
  const finished = steps.filter((s) => s.status === "VERIFIED" && s.result);
  const final = finished[finished.length - 1];
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
              : mode === "mock" ? "Demo mode — mock try-on, no credits used" : "API offline — start the backend"}
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
                  Each step starts from the previous result and is checked by OpenAI + Gemini{live ? "" : " (demo mode skips this)"}. {live && credits ? `Uses ${cost} FASHN credits.` : "Demo mode: free."}
                </p>
              </div>
            )}
            {!readiness.ok && <p className="mx-auto mt-3 max-w-xl rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{readiness.message}</p>}
          </section>
        )}

        {/* Result */}
        {run && (
          <section ref={resultRef} className="scroll-mt-6 space-y-6">
            <StepTitle n={5} title="Your try-on" hint={`${finished.length} of ${run.total_steps} products applied · ${label(run.status)}`} />
            {steps.some((s) => s.provider === "mock") && <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">Demo result — a placeholder, not a real try-on. No credits were used.</p>}
            {failed && (
              <p className="rounded-xl border border-red-300 bg-red-50 p-3 text-red-800">
                Step {failed.step} ({failed.product.name.slice(0, 50)}) {failed.status === "REJECTED" ? "was rejected by verification" : "failed"}: {failed.error}. Nothing was retried; later steps were skipped.
                {finished.length > 0 ? ` Showing the result after step ${finished.length}.` : ""}
              </p>
            )}
            <div className="grid gap-8 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              <div>
                {run.final_url ? (
                  <img src={`${API}${run.final_url}`} alt="Your try-on" className="w-full rounded-2xl border shadow-lg" />
                ) : (
                  <div className="flex h-96 items-center justify-center rounded-2xl border bg-white text-gray-400">{busy ? "Generating…" : "No image generated"}</div>
                )}
                <p className="mt-2 text-xs text-gray-500">{final?.provider === "fashn" ? "Raw FASHN output, unedited." : final ? "Mock output." : ""}</p>
              </div>
              <div className="space-y-5">
                {final?.result && (
                  <div className="rounded-2xl border bg-white p-4 shadow-sm">
                    <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Verification</p>
                    <p className={`mt-1 inline-block rounded-lg px-3 py-1 text-lg font-bold ${tone(final.result.verification.overall)}`}>
                      {label(final.result.verification.overall)}
                    </p>
                    <ul className="mt-3 space-y-1 text-sm">
                      {Object.entries(final.result.verification.checks).map(([k, v]) => (
                        <li key={k} className="flex items-center justify-between">
                          <span>{CHECK_LABELS[k] ?? k}</span>
                          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(v)}`}>{label(v)}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-3 text-xs text-gray-500">{run.summary}</p>
                    <p className="mt-1 text-xs text-gray-500">{final.result.verification.notes}</p>
                  </div>
                )}
                <div className="rounded-2xl border bg-white p-4 shadow-sm">
                  <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Shop this look</p>
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
              <p className="mb-2 text-xs font-bold uppercase tracking-widest text-gray-400">Product tracking</p>
              <div className="overflow-x-auto rounded-2xl border bg-white shadow-sm">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="p-3">Step</th><th className="p-3">Product</th><th className="p-3">Category</th>
                      <th className="p-3">Retailer · ID</th><th className="p-3">Generation</th><th className="p-3">Product in result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {steps.map((s) => (
                      <tr key={s.id} className="border-t">
                        <td className="p-3 font-bold">{s.step}</td>
                        <td className="p-3">
                          <div className="flex items-center gap-2">
                            <img src={`${API}${s.product.image_url}`} alt="" className="h-10 w-10 rounded bg-gray-50 object-contain" />
                            <span className="line-clamp-2 max-w-[220px]">{s.product.name}</span>
                          </div>
                        </td>
                        <td className="p-3">{CATEGORY_LABEL[s.product.category] ?? s.product.category}</td>
                        <td className="p-3 text-xs text-gray-600">{retailerName(s.product.retailer)} · {s.product.retailer_product_id.slice(0, 16)}</td>
                        <td className="p-3"><span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(s.status)}`}>{label(s.status)}</span></td>
                        <td className="p-3"><span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(s.product_presence)}`}>{label(s.product_presence)}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {steps.length > 1 && (
              <div>
                <p className="mb-2 text-xs font-bold uppercase tracking-widest text-gray-400">Step by step</p>
                <div className="flex gap-4 overflow-x-auto pb-2">
                  <figure className="w-40 shrink-0">
                    <img src={`${API}${run.person_url}`} alt="Original" className="h-52 w-full rounded-xl border object-cover" />
                    <figcaption className="mt-1 text-xs text-gray-500">Original</figcaption>
                  </figure>
                  {steps.map((s) => (
                    <figure key={s.id} className="w-40 shrink-0">
                      {s.result ? (
                        <img src={`${API}${s.result.url}`} alt={`Step ${s.step}`} className="h-52 w-full rounded-xl border object-cover" />
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
