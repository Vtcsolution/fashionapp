"use client";
/* eslint-disable @next/next/no-img-element */
import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Product = {
  retailer: string;
  product_id: string;
  name: string;
  price: string | null;
  currency: string | null;
  url: string;
  image_url: string;
  image_urls: string[];
};
type Job = {
  id: number;
  status: string;
  provider: string;
  error: string | null;
  person_url: string;
  product: { name: string; price: string | null; currency: string | null; retailer: string; url: string; image_url: string; source_image_url: string };
  result: null | { url: string; verification: { product: string; identity: string; overall: string; notes: string } };
};

const badge = (v: string) =>
  ["VERIFIED", "PRESERVED", "PASS"].includes(v)
    ? "bg-green-100 text-green-800"
    : ["FAIL", "CHANGED"].includes(v)
      ? "bg-red-100 text-red-800"
      : "bg-amber-100 text-amber-800";

export default function Page() {
  const [personPath, setPersonPath] = useState<string | null>(null);
  const [personUrl, setPersonUrl] = useState<string | null>(null);
  const [query, setQuery] = useState("blue floral women's dress");
  const [products, setProducts] = useState<Product[]>([]);
  const [retailerStatus, setRetailerStatus] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Product | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function call<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(`${API}${path}`, init);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
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

  const tryOn = () =>
    guard("Preparing product image…", async () => {
      if (!selected || !personPath) return;
      const sel = await call<{ id: number }>("/api/products/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selected),
      });
      setBusy("Running try-on…");
      let j = await call<Job>("/api/tryon", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product_id: sel.id, person_path: personPath }),
      });
      setJob(j);
      while (!["VERIFIED", "FAILED"].includes(j.status)) {
        await new Promise((r) => setTimeout(r, 1500));
        j = await call<Job>(`/api/tryon/${j.id}`);
        setJob(j);
      }
    });

  return (
    <main className="mx-auto max-w-6xl space-y-8 p-6">
      <header>
        <h1 className="text-2xl font-bold">TryOnU — Virtual Try-On prototype</h1>
        <p className="text-sm text-gray-600">One person + one real product → one try-on → one verified result.</p>
      </header>

      {error && <div className="rounded border border-red-300 bg-red-50 p-3 text-red-800">{error}</div>}
      {busy && <div className="rounded border bg-blue-50 p-3 text-blue-800">⏳ {busy}</div>}

      <section className="space-y-2">
        <h2 className="font-semibold">1. Your photo</h2>
        <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        {personUrl && <img src={personUrl} alt="You" className="h-48 rounded border object-cover" />}
      </section>

      <section className="space-y-2">
        <h2 className="font-semibold">2. Find a product</h2>
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
                <p>{p.price ? `${p.price} ${p.currency ?? ""}` : "—"} · <span className="uppercase text-gray-500">{p.retailer}</span></p>
                <div className="mt-1 flex items-center justify-between">
                  <a href={p.url} target="_blank" rel="noreferrer" className="text-blue-600 underline">Shop</a>
                  <button className="rounded border px-2 py-1" onClick={() => setSelected(p)}>{on ? "Selected" : "Select"}</button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="font-semibold">3. Try it on</h2>
        <button className="rounded bg-purple-700 px-5 py-2 text-white disabled:opacity-50" disabled={!personPath || !selected || !!busy} onClick={tryOn}>
          TRY IT ON
        </button>
        {!personPath && <p className="text-xs text-gray-500">Upload a photo first.</p>}
        {!selected && <p className="text-xs text-gray-500">Select one product.</p>}
      </section>

      {job && (
        <section className="space-y-3">
          <h2 className="font-semibold">Result — job #{job.id} · {job.status} · provider: {job.provider}</h2>
          {job.provider === "mock" && <p className="rounded bg-amber-50 p-2 text-sm text-amber-800">Mock mode: no real try-on was generated and no credits were used.</p>}
          {job.error && <p className="text-red-700">{job.error}</p>}
          <div className="grid gap-4 md:grid-cols-3">
            <figure><img src={`${API}${job.person_url}`} alt="Person" className="rounded border" /><figcaption className="text-sm">Original</figcaption></figure>
            <figure><img src={`${API}${job.product.image_url}`} alt="Product" className="rounded border" /><figcaption className="text-sm">{job.product.name} — <a className="text-blue-600 underline" href={job.product.url} target="_blank" rel="noreferrer">Shop</a></figcaption></figure>
            <figure>{job.result ? <img src={`${API}${job.result.url}`} alt="Try-on" className="rounded border" /> : <div className="flex h-64 items-center justify-center rounded border text-gray-400">…</div>}<figcaption className="text-sm">Raw try-on output</figcaption></figure>
          </div>
          {job.result && (
            <div className="space-y-1">
              {(["product", "identity", "overall"] as const).map((k) => (
                <p key={k} className="text-sm capitalize">{k}: <span className={`rounded px-2 py-0.5 font-semibold ${badge(job.result!.verification[k])}`}>{job.result!.verification[k]}</span></p>
              ))}
              <p className="text-xs text-gray-600">{job.result.verification.notes}</p>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
