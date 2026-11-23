export type Project={id:string;name:string;category:string;duration:number;width:number;height:number;transcript_words:number;source_url:string;safety:{duplicate:boolean;music:string;originality:string;monetization:string};created:string};
export type Word={start:number;end:number;text:string};
export type Clip={id:string;project_id:string;part:number;start:number;end:number;title:string;status:string;error:string;captions:Word[];metadata:Record<string,any>;has_export:boolean;preview_url:string|null};
export type Job={id:string;project_id:string;kind:string;status:string;progress:number;message:string};
export const categories=['GTA 6 gameplay','GTA 6 hidden details','GTA 6 funny moments','MrBeast gaming','IShowSpeed reactions','Gaming challenges','Gaming news and updates'];
export const time=(n:number)=>`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`;
export async function api<T=any>(url:string,options:RequestInit={}):Promise<T>{const r=await fetch('/api'+url,options);if(!r.ok){let j;try{j=await r.json()}catch{throw new Error(`Server error (${r.status})`)}throw new Error(typeof j.detail==='string'?j.detail:JSON.stringify(j.detail))}return r.json()}
export const json=(method:string,data:unknown)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
