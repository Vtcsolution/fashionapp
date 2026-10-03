"use client";
/* eslint-disable @next/next/no-img-element */
import { useEffect, useState } from "react";

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
  color_details: "Color / details correspondence",
  placement: "Placement",
  identity_preserved: "Person identity preserved",
};
const tone = (v: string) =>
  v === "PASS" ? "bg-green-100 text-green-800" : v === "FAIL" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800";
const shopUrl = (p: { url: string; affiliate_url: string | null }) => p.affiliate_url || p.url;
const price = (p: { price: string | null; currency: string | null }) => (p.price ? `${p.price} ${p.currency ?? ""}` : "—");

export default function Page() {
  const [mode, setMode] = useState<string>("");
  const [personPath, setPersonPath] = useState<string | null>(null);
  const [personUrl, setPersonUrl] = useState<string | null>(null);
  const [query, setQuery] = useState("blue floral women's dress");
  const [products, setProducts] = useState<Product[]>([]);
  const [retailerStatus, setRetailerStatus] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Product | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API}/api/health`).then((r) => r.json()).then((j) => setMode(j.vto_mode)).catch(() => setMode("offline"));
  }, []);

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
    guard("Uploading photo…", async () => {
      const fd = new FormData();
      fd.append("file", f);
      const j = await call<{ person_path: string; url: string }>("/api/uploads/person", { method: "POST", body: fd });
      setPersonPath(j.person_path);
      setPersonUrl(`${API}${j.url}`);
      setJob(null);
    });

  const search = () =>
    guard("Searching retailers…", async () => {
      const j = await call<{ products: Product[]; retailers: Record<string, string> }>(
        `/api/products/search?q=${encodeURIComponent(query)}`,
      );
      setProducts(j.products);
      setRetailerStatus(j.retailers);
      setSelected(null);
    });

  // One click = one request = at most one generation. The backend never retries, and neither does this page.
  const tryOn = () =>
    guard(mode === "live" ? "Running LIVE FASHN try-on (one generation)…" : "Running mock try-on…", async () => {
      if (!selected || !personPath) return;
      const sel = await call<{ id: number }>("/api/products/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selected),
      });
      setJob(
        await call<Job>("/api/tryon", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_id: sel.id, person_path: personPath }),
        }),
      );
    });

  const live = mode === "live";

  return (
    <main className="mx-auto max-w-6xl space-y-8 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold">TryOnU — Virtual Try-On MVP</h1>
        <p className="text-sm text-gray-600">One person + one real product → one try-on → review → shop the real product.</p>
        {mode && (
          <p className={`inline-block rounded px-2 py-1 text-xs font-semibold ${live ? "bg-red-100 text-red-800" : mode === "mock" ? "bg-amber-100 text-amber-800" : "bg-gray-200"}`}>
            {live ? "LIVE — a real FASHN generation will be requested" : mode === "mock" ? "MOCK MODE — no FASHN calls, no credits used" : "API offline"}
          </p>
        )}
      </header>

      {error && <div className="rounded border border-red-300 bg-red-50 p-3 text-red-800">{error}</div>}
      {busy && <div className="rounded border bg-blue-50 p-3 text-blue-800">⏳ {busy}</div>}

      <section className="space-y-2">
        <h2 className="font-semibold">A. Upload person</h2>
        <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        {personUrl && <img src={personUrl} alt="You" className="h-56 rounded border object-cover" />}
      </section>

      <section className="space-y-2">
        <h2 className="font-semibold">B. Search products</h2>
        <div className="flex gap-2">
          <input className="flex-1 rounded border p-2" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} />
          <button className="rounded bg-black px-4 py-2 text-white disabled:opacity-50" disabled={!!busy} onClick={search}>Search</button>
        </div>
        {Object.keys(retailerStatus).length > 0 && (
          <p className="text-xs text-gray-500">{Object.entries(retailerStatus).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>
        )}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {products.map((p) => {
            const on = selected?.retailer === p.retailer && selected.product_id === p.product_id;
            return (
              <div key={p.retailer + p.product_id} className={`rounded border p-2 text-sm ${on ? "ring-2 ring-black" : ""}`}>
                <img src={p.image_url} alt={p.name} className="h-48 w-full rounded object-contain" />
                <p className="mt-1 line-clamp-2 font-medium">{p.name}</p>
                <p>{price(p)} · <span className="uppercase text-gray-500">{p.retailer}</span></p>
                <div className="mt-1 flex items-center justify-between">
                  <a href={shopUrl(p)} target="_blank" rel="noreferrer" className="text-blue-600 underline">Shop</a>
                  <button className="rounded border px-2 py-1" onClick={() => setSelected(p)}>{on ? "Selected" : "Select"}</button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold">C. Selected product</h2>
        {personUrl && selected ? (
          <div className="flex flex-wrap items-center gap-6 rounded border p-3">
            <img src={personUrl} alt="Person" className="h-40 rounded border object-cover" />
            <span className="text-2xl">+</span>
            <img src={selected.image_url} alt={selected.name} className="h-40 rounded border object-contain" />
            <div className="space-y-1 text-sm">
              <p className="font-medium">{selected.name}</p>
              <p>{price(selected)} · <span className="uppercase">{selected.retailer}</span></p>
              <a href={shopUrl(selected)} target="_blank" rel="noreferrer" className="text-blue-600 underline">Open product page</a>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-500">Upload a photo and select one product.</p>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="font-semibold">D. Try on</h2>
        <button className="rounded bg-purple-700 px-5 py-2 text-white disabled:opacity-50" disabled={!personPath || !selected || !!busy} onClick={tryOn}>
          TRY ON
        </button>
      </section>

      {job && (
        <section className="space-y-3">
          <h2 className="font-semibold">E. Result — job #{job.id} · {job.status} · provider: {job.provider}</h2>
          {job.provider === "mock" && <p className="rounded bg-amber-50 p-2 text-sm text-amber-800">Mock result — not a real try-on. No credits were used.</p>}
          {job.error && <p className="rounded border border-red-300 bg-red-50 p-2 text-red-800">Failed: {job.error}. Nothing was retried.</p>}
          <div className="grid gap-4 md:grid-cols-3">
            <figure><img src={`${API}${job.person_url}`} alt="Person" className="rounded border" /><figcaption className="text-sm">Original</figcaption></figure>
            <figure><img src={`${API}${job.product.image_url}`} alt="Product" className="rounded border" /><figcaption className="text-sm">{job.product.name} · {price(job.product)}</figcaption></figure>
            <figure>
              {job.result ? <img src={`${API}${job.result.url}`} alt="Try-on" className="rounded border" /> : <div className="flex h-64 items-center justify-center rounded border text-gray-400">No result</div>}
              <figcaption className="text-sm">{job.provider === "fashn" ? "Raw FASHN output" : "Mock output"}</figcaption>
            </figure>
          </div>
          {job.result && (
            <div className="space-y-1">
              <p className="text-sm font-semibold">Verification: <span className={`rounded px-2 py-0.5 ${tone(job.result.verification.overall)}`}>{job.result.verification.overall.replace("_", " ")}</span></p>
              {Object.entries(job.result.verification.checks).map(([k, v]) => (
                <p key={k} className="text-sm">{CHECK_LABELS[k] ?? k}: <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone(v)}`}>{v.replace("_", " ")}</span></p>
              ))}
              <p className="text-xs text-gray-600">{job.result.verification.notes}</p>
            </div>
          )}
          <a href={shopUrl(job.product)} target="_blank" rel="noreferrer" className="inline-block rounded bg-green-700 px-5 py-2 text-white">Shop Product</a>
        </section>
      )}
    </main>
  );
}
