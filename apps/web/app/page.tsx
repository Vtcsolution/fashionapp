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
type Job = {
  id: number;
  step: number;
  status: string;
  provider: string;
  error: string | null;
  person_url: string;
  product: { name: string; price: string | null; currency: string | null; retailer: string; url: string; affiliate_url: string | null; image_url: string };
  result: null | { url: string; verification: Verification };
};

const CHECK_LABELS: Record<string, string> = {
  product_presence: "Product presence",
  product_correspondence: "Product correspondence",
  color_details: "Color / details",
  placement: "Placement",
  identity_preserved: "Identity preserved",
};
const tone = (v: string) =>
  v === "PASS" ? "bg-emerald-100 text-emerald-800" : v === "FAIL" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800";
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
  const [retailerStatus, setRetailerStatus] = useState<Record<string, string>>({});
  const [hasSearched, setHasSearched] = useState(false);
  const [selection, setSelection] = useState<Selection<Product>>({});
  const [jobs, setJobs] = useState<Job[]>([]); // one per try-on step, in order
  const [plan, setPlan] = useState(0); // how many steps this run intended
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
  const guard = async (label: string, fn: () => Promise<void>) => {
    setError("");
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
      setProgress("");
    }
  };

  const clearResults = () => {
    setJobs([]);
    setPlan(0);
  };
  const upload = (f: File) =>
    guard("Uploading your photo…", async () => {
      const fd = new FormData();
      fd.append("file", f);
      const j = await call<{ person_path: string; url: string }>("/api/uploads/person", { method: "POST", body: fd });
      setPersonPath(j.person_path);
      setPersonUrl(`${API}${j.url}`);
      clearResults();
    });
  const removePhoto = () => {
    setPersonPath(null);
    setPersonUrl(null);
    clearResults();
    if (fileRef.current) fileRef.current.value = "";
  };

  const search = () =>
    guard("Searching eBay and AliExpress…", async () => {
      const j = await call<{ products: Product[]; retailers: Record<string, string>; searched: string[] }>(
        `/api/products/search?q=${encodeURIComponent(prompt)}`,
      );
      setProducts(j.products);
      setRetailerStatus(j.retailers);
      setSearched(j.searched);
      setHasSearched(true);
      setSelection({});
      clearResults();
    });

  // Each product is its own request = its own generation. Step N starts from the raw result of step N-1.
  // The loop stops at the first failure. Nothing is ever retried, here or in the backend.
  const tryOn = () => {
    if (!readiness.ok || !personPath) return;
    if (live) {
      const msg = `This runs ${queue.length} REAL FASHN generation${queue.length === 1 ? "" : "s"} (${cost} credits). Continue?`;
      if (!window.confirm(msg)) return;
    }
    const items = [...queue];
    return guard(live ? "Creating your try-on…" : "Creating a mock try-on…", async () => {
      clearResults();
      setPlan(items.length);
      let base: number | null = null;
      const done: Job[] = [];
      for (let i = 0; i < items.length; i++) {
        setProgress(`Step ${i + 1} of ${items.length}: ${items[i].name.slice(0, 50)}`);
        const sel = await call<{ id: number }>("/api/products/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(items[i]),
        });
        const job: Job = await call<Job>("/api/tryon", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_id: sel.id, person_path: personPath, base_job_id: base }),
        });
        done.push(job);
        setJobs([...done]);
        setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
        if (job.status !== "VERIFIED") break; // stop on failure
        base = job.id;
      }
      refreshHealth();
    });
  };

  const scrollToCategory = (c: string) => document.getElementById(`cat-${c}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

  const finished = jobs.filter((j) => j.status === "VERIFIED" && j.result);
  const final = finished[finished.length - 1];
  const failed = jobs.find((j) => j.status === "FAILED");

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
                placeholder="e.g. black leather jacket with white sneakers"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && prompt.trim() && !busy && search()}
              />
              <button className="rounded-xl bg-violet-600 px-8 py-4 text-lg font-bold text-white shadow hover:bg-violet-700 disabled:opacity-50" disabled={!prompt.trim() || !!busy} onClick={search}>
                SEARCH
              </button>
            </div>
            <p className="mt-2 text-xs text-gray-500">Up to 4 items per search · try “red dress with black heels and handbag” · add a budget like “under $100”</p>
          </section>
        )}

        {/* 3. Products by category */}
        {personPath && hasSearched && (
          <section className="space-y-6">
            <StepTitle n={3} title="Pick your items" hint="One per category — pick as many categories as you like" />
            <p className="text-xs text-gray-500">
              Searched: {searched.map((s) => `“${s}”`).join(", ")} · {Object.entries(retailerStatus).map(([k, v]) => `${retailerName(k)}: ${v}`).join(" · ")}
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

        {/* 4. Selected items, in try-on order */}
        {personPath && queue.length > 0 && (
          <section>
            <StepTitle n={4} title="Your look" hint={`${queue.length} item${queue.length === 1 ? "" : "s"} · tried on one after another, in this order`} />
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

        {/* TRYON U */}
        {personPath && hasSearched && (
          <section className="text-center">
            <button
              onClick={tryOn}
              disabled={!readiness.ok || !!busy}
              className="rounded-2xl bg-gradient-to-r from-violet-600 to-fuchsia-600 px-14 py-5 text-2xl font-extrabold tracking-wide text-white shadow-lg transition hover:scale-[1.02] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100"
            >
              TRYON U{queue.length > 1 ? ` · ${queue.length} items` : ""}
            </button>
            {readiness.ok && queue.length > 1 && (
              <p className="mx-auto mt-3 max-w-xl text-sm text-gray-600">
                {queue.length} separate try-ons run in order; each starts from the previous result.
                {live && credits ? ` Uses ${cost} FASHN credits.` : " Demo mode: free."}
              </p>
            )}
            {readiness.ok && queue.length === 1 && live && credits && <p className="mt-3 text-sm text-gray-600">Uses {cost} FASHN credits.</p>}
            {!readiness.ok && <p className="mx-auto mt-3 max-w-xl rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{readiness.message}</p>}
          </section>
        )}

        {/* Result */}
        {jobs.length > 0 && (
          <section ref={resultRef} className="scroll-mt-6 space-y-6">
            <StepTitle n={5} title="Your try-on" hint={plan > 1 ? `${finished.length} of ${plan} steps done` : undefined} />
            {jobs.some((j) => j.provider === "mock") && <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">Demo result — a placeholder, not a real try-on. No credits were used.</p>}
            {failed && (
              <p className="rounded-xl border border-red-300 bg-red-50 p-3 text-red-800">
                Step {failed.step} ({failed.product.name.slice(0, 50)}) failed: {failed.error}. Nothing was retried.
                {finished.length > 0 ? ` Showing the result after step ${finished.length}.` : ""}
              </p>
            )}
            <div className="grid gap-8 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              <div>
                {final?.result ? (
                  <img src={`${API}${final.result.url}`} alt="Your try-on" className="w-full rounded-2xl border shadow-lg" />
                ) : (
                  <div className="flex h-96 items-center justify-center rounded-2xl border bg-white text-gray-400">{busy ? "Generating…" : "No image generated"}</div>
                )}
                <p className="mt-2 text-xs text-gray-500">{final?.provider === "fashn" ? "Raw FASHN output, unedited." : final ? "Mock output." : ""}</p>
              </div>
              <div className="space-y-5">
                {final?.result && (
                  <div className="rounded-2xl border bg-white p-4 shadow-sm">
                    <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Verification{plan > 1 ? ` · last step (${final.step})` : ""}</p>
                    <p className={`mt-1 inline-block rounded-lg px-3 py-1 text-lg font-bold ${tone(final.result.verification.overall)}`}>
                      {final.result.verification.overall.replace("_", " ")}
                    </p>
                    <ul className="mt-3 space-y-1 text-sm">
                      {Object.entries(final.result.verification.checks).map(([k, v]) => (
                        <li key={k} className="flex items-center justify-between">
                          <span>{CHECK_LABELS[k] ?? k}</span>
                          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(v)}`}>{v.replace("_", " ")}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-3 text-xs text-gray-500">{final.result.verification.notes}</p>
                  </div>
                )}
                <div className="rounded-2xl border bg-white p-4 shadow-sm">
                  <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Shop this look</p>
                  <div className="mt-2 space-y-3">
                    {jobs.map((j) => (
                      <div key={j.id} className="flex gap-3">
                        <img src={`${API}${j.product.image_url}`} alt={j.product.name} className="h-20 w-20 shrink-0 rounded-xl bg-gray-50 object-contain" />
                        <div className="min-w-0 flex-1 text-sm">
                          <p className="line-clamp-2 font-medium leading-5">{j.step}. {j.product.name}</p>
                          <p className="font-semibold">{price(j.product)} <span className="font-normal text-gray-500">· {retailerName(j.product.retailer)}</span></p>
                          <a href={shopUrl(j.product)} target="_blank" rel="noreferrer" className="mt-1 inline-block rounded-lg bg-black px-4 py-1.5 text-xs font-bold tracking-wide text-white hover:bg-gray-800">
                            SHOP PRODUCT
                          </a>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {jobs.length > 1 && (
              <div>
                <p className="mb-2 text-xs font-bold uppercase tracking-widest text-gray-400">Step by step</p>
                <div className="flex gap-4 overflow-x-auto pb-2">
                  <figure className="w-40 shrink-0">
                    <img src={`${API}${jobs[0].person_url}`} alt="Original" className="h-52 w-full rounded-xl border object-cover" />
                    <figcaption className="mt-1 text-xs text-gray-500">Original</figcaption>
                  </figure>
                  {jobs.map((j) => (
                    <figure key={j.id} className="w-40 shrink-0">
                      {j.result ? (
                        <img src={`${API}${j.result.url}`} alt={`Step ${j.step}`} className="h-52 w-full rounded-xl border object-cover" />
                      ) : (
                        <div className="flex h-52 items-center justify-center rounded-xl border bg-red-50 text-xs text-red-700">failed</div>
                      )}
                      <figcaption className="mt-1 text-xs text-gray-600">
                        {j.step}. {j.product.name.slice(0, 28)}
                        {j.result && <span className={`ml-1 rounded px-1 py-0.5 text-[10px] font-semibold ${tone(j.result.verification.overall)}`}>{j.result.verification.overall.replace("_", " ")}</span>}
                      </figcaption>
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
