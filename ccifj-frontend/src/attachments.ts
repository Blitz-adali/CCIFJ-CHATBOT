export type AttachmentProgress = (message: string) => void;

type TesseractWorker = { recognize: (image: string | HTMLCanvasElement) => Promise<{ data: { text: string } }>; terminate: () => Promise<void> };
type TesseractGlobal = { createWorker: (langs: string) => Promise<TesseractWorker> };
type MammothGlobal = { extractRawText: (options: {arrayBuffer: ArrayBuffer}) => Promise<{value: string}> };

function loadScript(src: string, globalName: string): Promise<void> {
  if ((window as unknown as Record<string, unknown>)[globalName]) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[data-ccifj-lib="${globalName}"]`);
    if (existing) { existing.addEventListener('load', () => resolve(), {once:true}); existing.addEventListener('error', () => reject(new Error(`Could not load ${globalName}.`)), {once:true}); return; }
    const script = document.createElement('script'); script.src = src; script.async = true; script.dataset.ccifjLib = globalName;
    script.onload = () => resolve(); script.onerror = () => reject(new Error(`Could not load ${globalName}. Check your internet connection and retry.`)); document.head.append(script);
  });
}

function normalize(text: string): string { return text.replace(/\u0000/g, '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim(); }

async function imageText(file: File, progress: AttachmentProgress): Promise<string> {
  progress(`Loading OCR for ${file.name}…`);
  await loadScript('https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/tesseract.min.js', 'Tesseract');
  const api = (window as unknown as {Tesseract:TesseractGlobal}).Tesseract;
  const worker = await api.createWorker('eng+fra+jpn');
  const objectUrl = URL.createObjectURL(file);
  try {
    const result = await worker.recognize(objectUrl);
    return result.data.text;
  } finally { URL.revokeObjectURL(objectUrl); await worker.terminate(); }
}

async function pdfText(file: File, progress: AttachmentProgress): Promise<string> {
  progress(`Reading PDF ${file.name}…`);
  const pdfjs = await import(/* @vite-ignore */ 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.min.mjs') as {
    GlobalWorkerOptions:{workerSrc:string}; getDocument:(args:{data:Uint8Array})=>{promise:Promise<{numPages:number;getPage:(page:number)=>Promise<{getTextContent:()=>Promise<{items:Array<{str?:string}>}>;getViewport:(args:{scale:number})=>{width:number;height:number};render:(args:{canvas:HTMLCanvasElement;canvasContext:CanvasRenderingContext2D;viewport:{width:number;height:number}})=>{promise:Promise<void>}}>}>};
  };
  pdfjs.GlobalWorkerOptions.workerSrc='https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.worker.min.mjs';
  const doc=await pdfjs.getDocument({data:new Uint8Array(await file.arrayBuffer())}).promise;
  if(doc.numPages>8) throw new Error(`${file.name} has ${doc.numPages} pages. Please attach a PDF with 8 pages or fewer.`);
  const pages:string[]=[]; let worker:TesseractWorker|undefined;
  try {
    for(let i=1;i<=doc.numPages;i++){
      progress(`Reading ${file.name} · page ${i} of ${doc.numPages}…`);
      const page=await doc.getPage(i); const text=(await page.getTextContent()).items.map(item=>item.str??'').join(' ').trim();
      if(text.length>30){pages.push(text);continue;}
      if(!worker){await loadScript('https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/tesseract.min.js','Tesseract');worker=await (window as unknown as {Tesseract:TesseractGlobal}).Tesseract.createWorker('eng+fra+jpn');}
      const viewport=page.getViewport({scale:1.5}); const canvas=document.createElement('canvas'); canvas.width=viewport.width;canvas.height=viewport.height;
      const context=canvas.getContext('2d');if(!context)throw new Error('Could not prepare a PDF page for OCR.');
      await page.render({canvas,canvasContext:context,viewport}).promise;pages.push((await worker.recognize(canvas)).data.text);canvas.width=0;canvas.height=0;
    }
  } finally {if(worker)await worker.terminate();}
  return pages.join('\n\n');
}

export async function extractAttachmentText(files: File[], progress: AttachmentProgress): Promise<string> {
  const results:string[]=[];
  for(const file of files){
    if(file.size>15*1024*1024)throw new Error(`${file.name} is larger than 15 MB.`);
    let text=''; const type=file.type.toLowerCase(); const name=file.name.toLowerCase();
    if(type.startsWith('image/')) text=await imageText(file,progress);
    else if(type==='application/pdf'||name.endsWith('.pdf')) text=await pdfText(file,progress);
    else if(name.endsWith('.docx')){
      progress(`Reading document ${file.name}…`);await loadScript('https://cdn.jsdelivr.net/npm/mammoth@1.9.0/mammoth.browser.min.js','mammoth');
      text=(await (window as unknown as {mammoth:MammothGlobal}).mammoth.extractRawText({arrayBuffer:await file.arrayBuffer()})).value;
    } else if(type.startsWith('text/')||/\.(txt|md|csv|json|xml|html)$/i.test(name)){progress(`Reading document ${file.name}…`);text=await file.text();}
    else throw new Error(`${file.name}: use an image, PDF, DOCX, TXT, Markdown, CSV, JSON, XML or HTML file.`);
    const clean=normalize(text);if(clean)results.push(`[Extracted from ${file.name}]\n${clean}`);
  }
  const combined=results.join('\n\n');
  if(!combined)throw new Error('No readable text was found in the selected files.');
  return combined.slice(0,3000);
}
