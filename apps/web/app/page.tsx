"use client";
/* eslint-disable @next/next/no-img-element */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CATEGORY_LABEL, groupByCategory, keyOf, removeCategory, selectedList, toggleSelect, tryOnReadiness, tryOnTarget,
  type Selection,
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
  const [personPath, setPersonPath] = useState<string | null>(null);
  const [personUrl, setPersonUrl] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [searched, setSearched] = useState<string[]>([]);
  const [retailerStatus, setRetailerStatus] = useState<Record<string, string>>({});
  const [hasSearched, setHasSearched] = useState(false);
  const [selection, setSelection] = useState<Selection<Product>>({});
  const [targetKey, setTargetKey] = useState<string | null>(null); // the ONE item to try on when several are selected
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API}/api/health`).then((r) => r.json()).then((j) => setMode(j.vto_mode)).catch(() => setMode("offline"));
  }, []);

  const groups = useMemo(() => groupByCategory(products), [products]);
  const picked = selectedList(selection);
  const target = tryOnTarget(selection, targetKey);
  const baseReadiness = tryOnReadiness(!!personPath, picked.length, !!target);
  // The first live test is restricted to eBay products by the backend guard; say so before the click.
  const nonEbayLive = mode === "live" && !!target && target.retailer !== "ebay";
  const readiness = nonEbayLive
    ? { ok: false, message: "The first live test needs an eBay product. Choose an eBay item to try on." }
    : baseReadiness;

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
    }
  };

  const upload = (f: File) =>
    guard("Uploading your photo…", async () => {
      const fd = new FormData();
      fd.append("file", f);
      const j = await call<{ person_path: string; url: string }>("/api/uploads/person", { method: "POST", body: fd });
      setPersonPath(j.person_path);
      setPersonUrl(`${API}${j.url}`);
      setJob(null);
    });
  const removePhoto = () => {
    setPersonPath(null);
    setPersonUrl(null);
    setJob(null);
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
      setJob(null);
    });

  // One click = one request = at most one generation. Never retried here or in the backend.
  const tryOn = () =>
    guard(mode === "live" ? "Creating your try-on…" : "Creating a mock try-on…", async () => {
      if (!readiness.ok || !personPath || !target) return;
      const chosen = target; // exactly one product is ever sent
      const sel = await call<{ id: number }>("/api/products/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(chosen),
      });
      setJob(
        await call<Job>("/api/tryon", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_id: sel.id, person_path: personPath }),
        }),
      );
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    });

  const scrollToCategory = (c: string) => document.getElementById(`cat-${c}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="min-h-screen bg-gradient-to-b from-violet-50 via-white to-white text-gray-900">
      <header className="mx-auto max-w-6xl px-6 pt-10 pb-4 text-center">
        <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">
          Try<span className="text-violet-600">On</span>U
        </h1>
        <p className="mt-2 text-lg text-gray-600">Describe an outfit. Pick real products. See yourself in them.</p>
        {mode && (
          <p className={`mt-3 inline-block rounded-full px-3 py-1 text-xs font-semibold ${mode === "live" ? "bg-red-100 text-red-800" : mode === "mock" ? "bg-amber-100 text-amber-800" : "bg-gray-200 text-gray-700"}`}>
            {mode === "live" ? "LIVE — real FASHN generation" : mode === "mock" ? "Demo mode — mock try-on, no credits used" : "API offline — start the backend"}
          </p>
        )}
      </header>

      <main className="mx-auto max-w-6xl space-y-12 px-6 pb-24">
        {error && <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-800">{error}</div>}
        {busy && <div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-violet-800">⏳ {busy}</div>}

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
            <p className="mt-2 text-xs text-gray-500">Try: “red dress with black heels and handbag” · add a budget like “under $100”</p>
          </section>
        )}

        {/* 3-5. Products by category */}
        {personPath && hasSearched && (
          <section className="space-y-6">
            <StepTitle n={3} title="Pick your items" hint="One per category" />
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

        {/* Selected items */}
        {personPath && picked.length > 0 && (
          <section>
            <StepTitle n={4} title="Your selected items" hint={`${picked.length} selected`} />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {picked.map((p) => (
                <div key={keyOf(p)} className={`flex gap-3 rounded-2xl border bg-white p-3 shadow-sm ${target && keyOf(target) === keyOf(p) && picked.length > 1 ? "border-fuchsia-600 ring-2 ring-fuchsia-500" : ""}`}>
                  <img src={p.image_url} alt={p.name} className="h-24 w-24 shrink-0 rounded-xl bg-gray-50 object-contain" />
                  <div className="min-w-0 flex-1 text-sm">
                    <p className="text-xs font-bold uppercase tracking-widest text-gray-400">{CATEGORY_LABEL[p.category] ?? p.category}</p>
                    <p className="line-clamp-2 font-medium leading-5">{p.name}</p>
                    <p className="font-semibold">{price(p)} <span className="font-normal text-gray-500">· {retailerName(p.retailer)}</span></p>
                    {picked.length > 1 && (
                      <button
                        onClick={() => setTargetKey(keyOf(p))}
                        className={`mt-1 rounded-lg px-2 py-1 text-xs font-semibold ${target && keyOf(target) === keyOf(p) ? "bg-fuchsia-600 text-white" : "border hover:bg-gray-50"}`}
                      >
                        {target && keyOf(target) === keyOf(p) ? "✓ Trying this one" : "Try this one"}
                      </button>
                    )}
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
              TRYON U
            </button>
            {!readiness.ok && <p className={`mx-auto mt-3 max-w-xl text-sm ${picked.length > 1 || nonEbayLive ? "rounded-lg bg-amber-50 p-3 text-amber-800" : "text-gray-500"}`}>{readiness.message}</p>}
            {readiness.ok && picked.length > 1 && target && (
              <p className="mx-auto mt-3 max-w-xl text-sm text-gray-600">
                Only <b>{target.name.slice(0, 60)}</b> will be tried on. Your other {picked.length - 1} picks stay saved for the multi-item version.
              </p>
            )}
          </section>
        )}

        {/* Result */}
        {job && (
          <section ref={resultRef} className="scroll-mt-6">
            <StepTitle n={5} title="Your try-on" />
            {job.provider === "mock" && <p className="mb-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-800">Demo result — this is a placeholder, not a real try-on. No credits were used.</p>}
            {job.error && <p className="mb-3 rounded-xl border border-red-300 bg-red-50 p-3 text-red-800">Try-on failed: {job.error}. Nothing was retried.</p>}
            <div className="grid gap-8 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              <div>
                {job.result ? (
                  <img src={`${API}${job.result.url}`} alt="Your try-on" className="w-full rounded-2xl border shadow-lg" />
                ) : (
                  <div className="flex h-96 items-center justify-center rounded-2xl border bg-white text-gray-400">No image generated</div>
                )}
                <p className="mt-2 text-xs text-gray-500">{job.provider === "fashn" ? "Raw FASHN output, unedited." : "Mock output."}</p>
              </div>
              <div className="space-y-5">
                {job.result && (
                  <div className="rounded-2xl border bg-white p-4 shadow-sm">
                    <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Verification</p>
                    <p className={`mt-1 inline-block rounded-lg px-3 py-1 text-lg font-bold ${tone(job.result.verification.overall)}`}>
                      {job.result.verification.overall.replace("_", " ")}
                    </p>
                    <ul className="mt-3 space-y-1 text-sm">
                      {Object.entries(job.result.verification.checks).map(([k, v]) => (
                        <li key={k} className="flex items-center justify-between">
                          <span>{CHECK_LABELS[k] ?? k}</span>
                          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(v)}`}>{v.replace("_", " ")}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-3 text-xs text-gray-500">{job.result.verification.notes}</p>
                  </div>
                )}
                <div className="rounded-2xl border bg-white p-4 shadow-sm">
                  <p className="text-xs font-bold uppercase tracking-widest text-gray-400">Product</p>
                  <div className="mt-2 flex gap-3">
                    <img src={`${API}${job.product.image_url}`} alt={job.product.name} className="h-28 w-28 rounded-xl bg-gray-50 object-contain" />
                    <div className="text-sm">
                      <p className="line-clamp-3 font-medium">{job.product.name}</p>
                      <p className="mt-1 font-semibold">{price(job.product)}</p>
                      <p className="text-gray-500">{retailerName(job.product.retailer)}</p>
                    </div>
                  </div>
                  <a href={shopUrl(job.product)} target="_blank" rel="noreferrer" className="mt-4 block rounded-xl bg-black py-3 text-center font-bold tracking-wide text-white hover:bg-gray-800">
                    SHOP PRODUCT
                  </a>
                </div>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
