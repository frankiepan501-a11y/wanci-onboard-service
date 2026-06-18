# -*- coding: utf-8 -*-
"""万词上线自动化服务 (L2). 飞书「万词上线申请」表 → n8n 触发 → 本服务全自动:
下载报表zip → 建/复用作战台 → 导词库(表1+表4) → 登记总台 → 拉listing文案 → 埋词审计HTML
→ 填表2/3/5/6 → 发对应运营 → 回写状态。 密钥全走 env(public repo 不内联)。"""
import os, io, re, json, time, uuid, zipfile, tempfile, glob, threading, urllib.request
from fastapi import FastAPI, Request

FEISHU_APP_ID=os.environ["FEISHU_APP_ID"]; FEISHU_APP_SECRET=os.environ["FEISHU_APP_SECRET"]
PROXY=os.environ.get("LX_PROXY","https://frankiepan501.zeabur.app/webhook/lingxing-proxy")
PROXY_TOK=os.environ["LX_PROXY_TOKEN"]
TEMPLATE_APP=os.environ.get("WANCI_TEMPLATE_APP","FcycbOqACaimScsAMlCcSuDznJb")  # 食人花dock-北美 6表模板源
REG_APP=os.environ.get("WANCI_REG_APP","W8LPboJSMaVqlwsizQ8cPVDIn2c")
REG_TB=os.environ.get("WANCI_REG_TB","tbl2g78DcPnxWNwO")
APPLY_TB=os.environ.get("WANCI_APPLY_TB","tblPXS4uO8lK9p5g")
RANK_BASE=os.environ.get("WANCI_RANK_BASE","EEKNbZ8b8aqv6msOaTscotBDn5f")
AUTH_TOKEN=os.environ.get("ONBOARD_TOKEN","")
BASE="https://open.feishu.cn/open-apis"
OP_OID={  # 负责运营 → 聪哥1号 open_id (路由HTML)
 "陈翔宇":"ou_9c322382284a7a6672a091b9f4c0a551","林明坚":"ou_35aa6883c0598bac5c7e06fcb06f7c4d",
 "余培霓":"ou_40ff10b05fc358f88c5674f053665551","潘志聪":"ou_629ce01f4bc31de078e10fcb038dbf78"}

# ───────────────── 飞书 / 领星 helpers ─────────────────
_tok={"v":None,"t":0}
def tok():
    if _tok["v"] and time.time()-_tok["t"]<5400: return _tok["v"]
    r=urllib.request.urlopen(urllib.request.Request(BASE+"/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id":FEISHU_APP_ID,"app_secret":FEISHU_APP_SECRET}).encode(),headers={"Content-Type":"application/json"}))
    v=json.load(r)["tenant_access_token"]; _tok.update(v=v,t=time.time()); return v
def api(m,p,b=None):
    d=json.dumps(b).encode() if b is not None else None
    req=urllib.request.Request(BASE+p,data=d,method=m,headers={"Authorization":"Bearer "+tok(),"Content-Type":"application/json"})
    try: return json.load(urllib.request.urlopen(req,timeout=60))
    except urllib.error.HTTPError as e: return {"_http":e.code,"_body":e.read().decode()[:300]}
def ext(v):
    if isinstance(v,list) and v and isinstance(v[0],dict): return v[0].get("text","")
    return v if isinstance(v,(str,int,float)) else ""
def lall(app,tb):
    out=[];pt=""
    while True:
        u=BASE+f"/bitable/v1/apps/{app}/tables/{tb}/records?page_size=500"+(("&page_token="+pt) if pt else "")
        d=json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"Authorization":"Bearer "+tok()}),timeout=60))["data"]
        out+=d.get("items",[])
        if d.get("has_more"): pt=d["page_token"]
        else: break
    return out
def batch(app,tb,recs):
    n=0
    for i in range(0,len(recs),200):
        r=api("POST",f"/bitable/v1/apps/{app}/tables/{tb}/records/batch_create",{"records":[{"fields":f} for f in recs[i:i+200]]})
        if "data" not in r: raise RuntimeError("batch fail "+json.dumps(r,ensure_ascii=False)[:200])
        n+=len(recs[i:i+200]); time.sleep(0.25)
    return n
def clear(app,tb,pred=None):
    ids=[r["record_id"] for r in lall(app,tb) if (pred is None or pred(r["fields"]))]
    for i in range(0,len(ids),200):
        api("POST",f"/bitable/v1/apps/{app}/tables/{tb}/records/batch_delete",{"records":ids[i:i+200]}); time.sleep(0.2)
def ensure_site(app,tb):
    have={f["field_name"] for f in api("GET",f"/bitable/v1/apps/{app}/tables/{tb}/fields?page_size=200")["data"]["items"]}
    if "站点" not in have:
        api("POST",f"/bitable/v1/apps/{app}/tables/{tb}/fields",{"field_name":"站点","type":3,"property":{"options":[{"name":s} for s in ["US","CA","MX","JP","UK","DE","FR","IT","ES","AU","BR"]]}})
def upd(app,tb,rid,fields): return api("PUT",f"/bitable/v1/apps/{app}/tables/{tb}/records/{rid}",{"fields":fields})
def lx(path,params):
    body=json.dumps({"method":"POST","path":path,"params":params}).encode(); last=None
    for _ in range(4):
        try:
            req=urllib.request.Request(PROXY,data=body,method="POST",headers={"Content-Type":"application/json","Authorization":"Bearer "+PROXY_TOK})
            return json.load(urllib.request.urlopen(req,timeout=120))
        except Exception as e: last=e; time.sleep(3)
    raise last
def download_media(file_token,dest):
    url=BASE+f"/drive/v1/medias/{file_token}/download"
    req=urllib.request.Request(url,headers={"Authorization":"Bearer "+tok()})
    data=urllib.request.urlopen(req,timeout=120).read()
    io.open(dest,"wb").write(data); return dest
def im_text(oid,text): api("POST","/im/v1/messages?receive_id_type=open_id",{"receive_id":oid,"msg_type":"text","content":json.dumps({"text":text},ensure_ascii=False)})
def upload_file(path,name):
    boundary="----wanci"+uuid.uuid4().hex; data=io.open(path,"rb").read()
    pre=("--"+boundary+"\r\nContent-Disposition: form-data; name=\"file_type\"\r\n\r\nstream\r\n"
         "--"+boundary+"\r\nContent-Disposition: form-data; name=\"file_name\"\r\n\r\n"+name+"\r\n").encode()
    head=("--"+boundary+"\r\nContent-Disposition: form-data; name=\"file\"; filename=\""+name+"\"\r\nContent-Type: text/html\r\n\r\n").encode()
    body=pre+head+data+("\r\n--"+boundary+"--\r\n").encode()
    req=urllib.request.Request(BASE+"/im/v1/files",data=body,method="POST",headers={"Authorization":"Bearer "+tok(),"Content-Type":"multipart/form-data; boundary="+boundary})
    return json.load(urllib.request.urlopen(req,timeout=120))["data"]["file_key"]
def im_file(oid,fk): api("POST","/im/v1/messages?receive_id_type=open_id",{"receive_id":oid,"msg_type":"file","content":json.dumps({"file_key":fk})})

# ───────────────── 埋词相关性引擎 (与本地 audit_listing 一致) ─────────────────
STOP=set(['for','with','the','and','a','of','to','in','on','one','up','it','is','our','you','your','no','will'])
def stem(w):
    if len(w)<=3: return w
    if w.endswith('ies') and len(w)>4: return w[:-3]+'y'
    if w.endswith('sses') or w.endswith('shes') or w.endswith('ches') or w.endswith('xes') or w.endswith('zes'): return w[:-2]
    if w.endswith('s') and not w.endswith(('ss','us','is')): return w[:-1]
    return w
def toks(s): return [stem(w) for w in re.findall(r'[a-z0-9]+',str(s).lower().replace('switch2','switch 2')) if (len(w)>1 or w.isdigit()) and w not in STOP]
CAT_ANCHORS={
 "case":["case","holder","storage","organizer","etui","funda","estuche","aufbewahrung","carrying","pouch","sleeve","tasche","hülle","hulle","schutz","cartridge","custodia","porta","card box","game card holder"],
 "controller":["controller","controllers","control","controles","mando","mandos","manette","manettes","gamepad","joystick","joy stick","hall effect","hall-effect","kontroller","joypad"],
 "dock":["dock","docking","station","ladestation","stand","ständer","stander","cradle","tv dock","charging dock","charger stand","charger dock","soporte","halterung","mount"]}
IP_SENS=["piranha plant","piranha flower","mario","zelda","pokemon","pikmin","hello kitty","kirby","luigi","peach","bowser","amiibo","tomodachi","resident evil","smash bros","indiana jones","just dance","starfox","star fox","metroid","splatoon","donkey kong","animal crossing","pokopia","sonic","kart","gengar"]
OTHER_PLATFORM=["pc","xbox","ps5","ps4","ps3","play 4","play 5","play station","playstation","dualsense","steam deck","steamdeck"," steam","vr glasses","3ds","psp","nintendo ds"," ds ","dsi","android","celular"," phone","movil","móvil","gamecube","game cube","n64","nintendo 64"," 64 ","ps portal","portal","yoto","ipad","iphone","raspberry"]
PURE_CONSOLE=["console","consola","konsole","bundle"," games","switch games","spiele "]
COMP_BRANDS=["8bitdo","8 bit do","8bit do","gamesir","razer","gulikit","nyxi","mobapad","hori ","ipega","flydigi","binbok","powera","pdp ","nyko","iine","kingkong","easysmx","voyee","nitro deck","jsaux","genki","antank","belkin","tomtoc","spigen","dbrand","mooroer","fintie","procase","orzly","skull & co","skull and co","geekshare","playvital","geekria","mumba","younik","hyperkin"]
MACHINE_TOK=set(["nintendo","switch","2","oled","lite","1","one"])
def is_ip(k): kl=" "+k.lower()+" "; return any(n in kl for n in IP_SENS)
def is_other_platform(k): kl=" "+k.lower()+" "; return any(n in kl for n in OTHER_PLATFORM)
def is_pure_console(k): kl=" "+k.lower()+" "; return any(n in kl for n in PURE_CONSOLE)
def is_comp(k): kl=" "+k.lower()+" "; return any(b in kl for b in COMP_BRANDS)
def is_machine_compat(k): t=set(re.findall(r'[a-z0-9]+',k.lower())); return bool(t) and t.issubset(MACHINE_TOK)
def supported_machines(text):
    t=" "+text.lower()+" "; s={"2"}
    if "lite" in t: s.add("lite")
    if any(x in t for x in ["switch 1","switch one","original switch","first gen","switch1"]): s.add("1")
    return s
def incompatible_machine(kw,supp):
    k=" "+kw.lower()+" "
    if "lite" in k and "lite" not in supp: return True
    if (" switch 1 " in k or "switch one" in k or "switch 1 " in k) and "1" not in supp: return True
    return False
def qualify_embed(kw,cat,supp):
    k=kw.lower()
    if is_other_platform(k) or is_pure_console(k) or is_ip(k): return False
    if incompatible_machine(k,supp): return False
    if is_machine_compat(k): return True
    return any(a in k for a in CAT_ANCHORS.get(cat,CAT_ANCHORS["dock"]))

def load_listing(d):
    info=(d.get("data") or [{}])[0].get("info",{}); at=info.get("attributes",{}) or {}
    def g(k):
        v=at.get(k)
        if isinstance(v,list): return [(x.get("value") if isinstance(x,dict) else x) for x in v]
        return v
    def s1(v): return " ".join(str(x) for x in v if x) if isinstance(v,list) else (str(v) if v is not None else "")
    bl=g("bullet_point") or []
    if not isinstance(bl,list): bl=[bl]
    su=info.get("summaries",[{}])
    return {"title":s1(g("item_name")),"bullets":[b for b in bl if b],"desc":s1(g("product_description")),
            "st":s1(g("generic_keyword")),"status":(su[0] if su else {}).get("status",[])}

# 列位 & 报表解析 (与 import_seller_sprite 一致)
REV={"kw":0,"nat":9,"ad":12,"vol":16,"spr":17,"buy":20,"demand":24,"ppc":28,"top10":30}
MIN={"kw":0,"vol":6,"buy":8,"spr":11,"demand":14,"ppc":18,"top10":33}
ABA={"kw":0,"vol":2,"ppc":7,"spr":11,"top10":18}
TERM_HDRS=["用户搜索词","客户搜索词","customer search term","search term","搜索词"]
ORDER_HDRS=["广告订单","7 day total orders (#)","7天总订单数(#)","total orders","订单数","订单量"]
IPg=["zelda","mario","pokemon","pikachu","kirby","minecraft","rosalina","yoshi","splatoon","metroid","sonic","dave","diver","luminex","animal crossing"]
PRICEg=["used","refurbished","renewed","deals","cheap","clearance","segunda mano","usado","reacondicionado","barato","oferta"]
COMPg=["8bitdo","gamesir","nyxi","mobapad","jsaux","genki","antank","binbok","ponkor","hori","oivo","gulikit","kdd","younik","natuk","jingmai","fastsnail","nexigo"]
GIFTg=["gift","gifts","regalo","regalos"]
PLATFORMg=set(["switch","nintendo switch","switch 2","nintendo switch 2","nintendo","switch oled","nintendo switch oled","switch lite","nintendo switch lite","switch 2 console","nintendo switch 2 console","consola switch","consola nintendo switch"])
def matrix(kw):
    k=kw.lower().strip()
    if any(w in k for w in IPg): return "IP词"
    if any(w in k for w in PRICEg): return "排除-价格二手"
    if any(w in k for w in COMPg): return "品牌词-竞品"
    if any(w in k for w in GIFTg): return "礼品词"
    if k in PLATFORMg: return "品牌词-平台"
    return "意图词"
def tier(v):
    if v is None: return None
    return "大词" if v>=10000 else ("中词" if v>=1000 else "小词")
def numv(x):
    if x is None: return None
    s=str(x).strip()
    if s in ("","--","None","前3页无排名","前三页无排名"): return None
    try: return float(s)
    except: return None
def cur(x):
    if x is None: return None
    m=re.search(r"([\d.]+)",str(x)); return float(m.group(1)) if m else None
def xrows(p):
    import openpyxl
    wb=openpyxl.load_workbook(p,read_only=True,data_only=True); ws=wb.active
    it=ws.iter_rows(values_only=True); hdr=list(next(it)); rs=[r for r in it]
    wb.close(); return hdr,rs

# ───────────────── 报表导入 (港 import_seller_sprite) ─────────────────
def classify_report(bn,self_asin):
    if bn.startswith("ReverseASIN-"): return "self" if self_asin in bn else "comp"
    if bn.startswith("KeywordMining-"): return "mining"
    if bn.startswith("ABAKeywordTrend-"): return "aba"
    if bn.startswith("FUNLAB-") or bn.startswith("Fanlepu-"): return "sp_ss"
    if bn.startswith("BusinessReport"): return "biz"
    if "Search_term_report" in bn: return "sp_amz"
    return "sp_amz"  # lin 模式: 余下=亚马逊原生; (新建产品报表多为林明坚式)
def import_keywords(files,site,asin):
    merged={}
    def get(kw):
        key=kw.strip().lower()
        if key not in merged: merged[key]={"关键词":kw.strip(),"站点":site,"_src":set()}
        return merged[key]
    self_ranks={}
    for p in files["self"]:
        for r in xrows(p)[1]:
            if not r or r[0] is None: continue
            d=get(str(r[REV["kw"]])); d["_src"].add("自家反查")
            v=numv(r[REV["vol"]]);
            if v is not None: d["月搜索量"]=max(d.get("月搜索量",0),v)
            nat=numv(r[REV["nat"]]);
            if nat is not None: d["我方自然排名"]=nat; self_ranks[d["关键词"]]=nat
            if numv(r[REV["ad"]]) is not None: d["我方广告排名"]=numv(r[REV["ad"]])
            if numv(r[REV["spr"]]) is not None: d["SPR"]=numv(r[REV["spr"]])
            if numv(r[REV["demand"]]) is not None: d["需供比"]=numv(r[REV["demand"]])
            if numv(r[REV["buy"]]) is not None: d["CVR%"]=round(numv(r[REV["buy"]])*100,2)
            if cur(r[REV["ppc"]]) is not None: d["CPC$"]=cur(r[REV["ppc"]])
            if r[REV["top10"]]: d["竞品前十ASIN"]=str(r[REV["top10"]])
    for p in files["comp"]:
        for r in xrows(p)[1]:
            if not r or r[0] is None: continue
            d=get(str(r[REV["kw"]])); d["_src"].add("竞品反查")
            v=numv(r[REV["vol"]]);
            if v is not None: d["月搜索量"]=max(d.get("月搜索量",0),v)
            nat=numv(r[REV["nat"]])
            if nat is not None and (d.get("竞品最佳排名") is None or nat<d["竞品最佳排名"]): d["竞品最佳排名"]=nat
            for key,idx,f in [("SPR",REV["spr"],numv),("需供比",REV["demand"],numv),("CPC$",REV["ppc"],cur)]:
                if key not in d and f(r[idx]) is not None: d[key]=f(r[idx])
            if "CVR%" not in d and numv(r[REV["buy"]]) is not None: d["CVR%"]=round(numv(r[REV["buy"]])*100,2)
            if "竞品前十ASIN" not in d and r[REV["top10"]]: d["竞品前十ASIN"]=str(r[REV["top10"]])
    for p in files["mining"]:
        for r in xrows(p)[1]:
            if not r or r[0] is None: continue
            d=get(str(r[MIN["kw"]])); d["_src"].add("挖掘")
            v=numv(r[MIN["vol"]]);
            if v is not None: d["月搜索量"]=max(d.get("月搜索量",0),v)
            for key,idx,f in [("SPR",MIN["spr"],numv),("需供比",MIN["demand"],numv),("CPC$",MIN["ppc"],cur)]:
                if key not in d and f(r[idx]) is not None: d[key]=f(r[idx])
            if "CVR%" not in d and numv(r[MIN["buy"]]) is not None: d["CVR%"]=round(numv(r[MIN["buy"]])*100,2)
            if "竞品前十ASIN" not in d and r[MIN["top10"]]: d["竞品前十ASIN"]=str(r[MIN["top10"]])
    for p in files["aba"]:
        for r in xrows(p)[1]:
            if not r or r[0] is None or str(r[ABA["kw"]]).strip() in ("","None"): continue
            d=get(str(r[ABA["kw"]])); d["_src"].add("ABA")
            v=numv(r[ABA["vol"]]);
            if v is not None: d["月搜索量"]=max(d.get("月搜索量",0),v)
            if "SPR" not in d and numv(r[ABA["spr"]]) is not None: d["SPR"]=numv(r[ABA["spr"]])
            if "CPC$" not in d and cur(r[ABA["ppc"]]) is not None: d["CPC$"]=cur(r[ABA["ppc"]])
            if "竞品前十ASIN" not in d and r[ABA["top10"]]: d["竞品前十ASIN"]=str(r[ABA["top10"]])
    sp_orders={}
    for p in files["sp_amz"]+files["sp_ss"]:
        hdr,rs=xrows(p)
        def fc(cands):
            for i,h in enumerate(hdr):
                if str(h).strip().lower() in cands: return i
            for i,h in enumerate(hdr):
                if any(c in str(h).strip().lower() for c in cands): return i
            return None
        ti=fc(TERM_HDRS); oi=fc(ORDER_HDRS)
        if ti is None or oi is None: continue
        for r in rs:
            if len(r)<=max(ti,oi): continue
            term=r[ti]
            if not term or str(term).strip() in ("","--","None"): continue
            od=numv(r[oi]) or 0
            if od>0:
                k=str(term).strip().lower(); sp_orders[k]=sp_orders.get(k,0)+od
    for kl,od in sp_orders.items():
        d=get(kl); d["_src"].add("广告出单"); d["已出单单量"]=od
    SRC={"自家反查":"卖家精灵-自家反查","竞品反查":"卖家精灵-竞品反查","挖掘":"卖家精灵-挖掘","广告出单":"广告搜索词(已出单)","ABA":"ABA品牌分析"}
    TODAY=int(time.time()*1000)
    t1=[]
    for d in merged.values():
        kw=d["关键词"]; d["矩阵"]=matrix(kw)
        tv=tier(d.get("月搜索量"));
        if tv: d["词级"]=tv
        d["来源"]=";".join(SRC[s] for s in sorted(d["_src"])); d["数据更新日"]=TODAY; d.pop("_src",None)
        if d.get("月搜索量")==0: d.pop("月搜索量",None)
        t1.append(d)
    t4=[{"关键词":kw,"站点":site,"自然排名":nat,"是否收录":True,"快照日期":TODAY,"距首页差距":str(max(0,int(nat-16)))} for kw,nat in self_ranks.items()]
    return t1,t4

def ensure_t1_extra(app,t1):
    have={f["field_name"] for f in api("GET",f"/bitable/v1/apps/{app}/tables/{t1}/fields?page_size=200")["data"]["items"]}
    for n,ty,prop in [("SPR",2,{"formatter":"0"}),("我方广告排名",2,{"formatter":"0"}),("竞品前十ASIN",1,None)]:
        if n not in have:
            b={"field_name":n,"type":ty};
            if prop: b["property"]=prop
            api("POST",f"/bitable/v1/apps/{app}/tables/{t1}/fields",b)

# ───────────────── clone 作战台 (港 clone_warzone) ─────────────────
def clone_app(name):
    src=[t for t in api("GET",f"/bitable/v1/apps/{TEMPLATE_APP}/tables?page_size=100")["data"]["items"] if t["name"].startswith("表")]
    src.sort(key=lambda t:t["name"]); schema=[]
    for t in src:
        fs=api("GET",f"/bitable/v1/apps/{TEMPLATE_APP}/tables/{t['table_id']}/fields?page_size=200")["data"]["items"]
        cf=[]
        for f in fs:
            o={"field_name":f["field_name"],"type":f["type"]}; prop=f.get("property") or {}; np={}
            if f["type"] in (3,4): np["options"]=[{"name":x["name"]} for x in prop.get("options",[])]
            elif f["type"]==2 and prop.get("formatter"): np["formatter"]=prop["formatter"]
            elif f["type"]==5:
                if prop.get("date_formatter"): np["date_formatter"]=prop["date_formatter"]
                np["auto_fill"]=prop.get("auto_fill",False)
            if np: o["property"]=np
            cf.append(o)
        schema.append((t["name"],cf))
    app=api("POST","/bitable/v1/apps",{"name":name})["data"]["app"]["app_token"]
    tmap={}
    for tn,fs in schema:
        tmap[tn]=api("POST",f"/bitable/v1/apps/{app}/tables",{"table":{"name":tn,"fields":fs}})["data"]["table_id"]; time.sleep(0.3)
    for t in api("GET",f"/bitable/v1/apps/{app}/tables?page_size=100")["data"]["items"]:
        if t["name"]=="数据表": api("DELETE",f"/bitable/v1/apps/{app}/tables/{t['table_id']}")
    FRANKIE="ou_629ce01f4bc31de078e10fcb038dbf78"
    api("POST",f"/drive/v1/permissions/{app}/members?type=bitable&need_notification=false",{"member_type":"openid","member_id":FRANKIE,"perm":"full_access"})
    api("POST",f"/wiki/v2/spaces/7610698300903214305/nodes/move_docs_to_wiki",{"parent_wiki_token":"VgfDwDtAGibw6akdDuCcMTs2nLd","obj_type":"bitable","obj_token":app})
    api("POST",f"/drive/v1/permissions/{app}/members/transfer_owner?type=bitable",{"member_type":"openid","member_id":FRANKIE})
    return app,tmap

# ───────────────── HTML 审计 (港 audit_listing make) ─────────────────
def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def make_html(product,site,asin,store,L,rows,cat):
    supp=supported_machines(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"])
    tt=set(toks(L["title"])); bt=set()
    for b in L["bullets"]: bt|=set(toks(b))
    dt=set(toks(L["desc"])); st=set(toks(L["st"])); front=tt|bt|dt
    def cov(kw,s): k=toks(kw); return bool(k) and all(w in s for w in k)
    R=[]
    for r in rows:
        f=r["fields"]; kw=ext(f.get("关键词"))
        R.append({"kw":kw,"mx":f.get("矩阵"),"vol":float(ext(f.get("月搜索量")) or 0),"ord":float(ext(f.get("已出单单量")) or 0),
                  "rank":float(ext(f.get("我方自然排名")) or 0),"front":cov(kw,front),"instr":cov(kw,st),"qual":qualify_embed(kw,cat,supp)})
    total=len(R); embedded=sum(1 for r in R if r["front"] or r["instr"])
    rk=[r for r in R if r["rank"]>0]; p1=[r for r in rk if r["rank"]<=16]; p23=[r for r in rk if 16<r["rank"]<=48]; deep=[r for r in rk if r["rank"]>48]
    sens=lambda r: r["mx"] in ("IP词","品牌词-竞品") or is_ip(r["kw"]) or is_comp(r["kw"])
    ugc=[r for r in R if sens(r)]; embeddable=[r for r in R if r["qual"] and not sens(r)]; noise=[r for r in R if (not r["qual"]) and not sens(r)]
    fit=len(embeddable)+len(ugc)
    miss=sorted([r for r in embeddable if not(r["front"] or r["instr"])],key=lambda r:-(r["vol"]+r["ord"]*5000))
    missu=sorted([r for r in ugc if not(r["front"] or r["instr"])],key=lambda r:-(r["vol"]+r["ord"]*5000))
    nz=sorted(noise,key=lambda r:-r["vol"])
    be=len(L["bullets"])==0; de=not L["desc"].strip(); se=not L["st"].strip(); buy="BUYABLE" in (L["status"] or [])
    notext=(not L["title"].strip()) and be and de and se
    rkp=round(100.0*len(rk)/max(total,1)); ep=round(100.0*embedded/max(total,1))
    def trow(r):
        v="{:,}".format(int(r["vol"])) if r["vol"] else "<span class='dash'>—</span>"; o=str(int(r["ord"])) if r["ord"] else "<span class='dash'>—</span>"
        return f"<tr><td class='kw'>{esc(r['kw'])}</td><td><span class='tag'>{esc(r['mx'])}</span></td><td class='num'>{v}</td><td class='num'>{o}</td></tr>"
    miss_h="\n".join(trow(r) for r in miss[:20])
    ugc_h="\n".join(f"<li><span class='kw'>{esc(r['kw'])}</span> <span class='tag p'>{esc(r['mx'])}</span> 出单 {int(r['ord']) if r['ord'] else 0} → 引导 Review/QA</li>" for r in missu[:12]) or "<li>（无）</li>"
    nz_h="\n".join(f"<tr><td class='kw' style='color:#8b94a3'>{esc(r['kw'])}</td><td><span class='tag n'>{esc(r['mx'])}→疑噪</span></td><td class='num'>{('{:,}'.format(int(r['vol']))) if r['vol'] else '—'}</td></tr>" for r in nz[:15]) or "<tr><td colspan=3 style='color:#6b7280'>（无）</td></tr>"
    if notext:
        hb=f"""<div class="callout c-red"><h2 style="margin-top:0">🔴 领星未拉到该 listing 文案（标题/五点/描述/ST 全空）</h2><ul><li>状态 <strong>{esc('/'.join(L['status']) or '未知')}</strong> 无任何文案字段。</li><li>可能 listing 未建全 或 领星未同步,请运营核实后台。本次无法做埋词覆盖分析;下方「已收录」仍有效。</li></ul></div>"""
    elif be or de or se or not buy:
        mp=[x for x,c in [("五点空",be),("描述空",de),("后台ST空",se),("非BUYABLE",not buy)] if c]
        hb=f"""<div class="callout c-red"><h2 style="margin-top:0">🔴 头号问题：listing 是「半成品」</h2><ul><li><strong>{esc(' / '.join(mp))}</strong></li><li>能埋词的层严重缺失 → 多数词无处收录,先补全文案/上架可售。</li></ul></div>"""
    else: hb=""
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>{esc(product)} · {esc(site)} 埋词审计</title>
<style>:root{{--bg:#0f1115;--card:#171a21;--line:#262b36;--txt:#e6e9ef;--mut:#9aa3b2;--red:#ff5c66;--redbg:#2a161a;--grn:#28d6a3;--yel:#ffc24b;--yelbg:#2a2310;--blu:#5ab0ff;--accent:#19E0CE}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.65}}.wrap{{max-width:900px;margin:0 auto;padding:40px 24px 80px}}header{{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:26px}}.kicker{{color:var(--accent);font-size:13px;letter-spacing:2px;font-weight:600}}h1{{font-size:27px;margin:8px 0 6px}}.meta{{color:var(--mut);font-size:13px;font-family:Consolas,monospace}}h2{{font-size:19px;margin:30px 0 12px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin:12px 0}}.callout{{border-left:4px solid var(--red);background:var(--redbg);border-radius:10px;padding:16px 20px;margin:14px 0}}.callout h2{{color:var(--red)}}.c-yel{{background:var(--yelbg);border-color:var(--yel)}}.stat-row{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}.stat{{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px}}.stat .n{{font-size:30px;font-weight:700;color:var(--accent)}}.stat .l{{color:var(--mut);font-size:12.5px;margin-top:2px}}.tier{{display:flex;gap:10px;margin-top:10px}}.tierbox{{flex:1;text-align:center;background:#1b1f27;border-radius:9px;padding:9px 4px}}.tierbox .tn{{font-size:20px;font-weight:700}}.tierbox .tl{{font-size:11px;color:var(--mut)}}.t-good .tn{{color:var(--grn)}}.t-mid .tn{{color:var(--yel)}}.t-bad .tn{{color:var(--red)}}table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}}th{{text-align:left;color:var(--mut);border-bottom:1px solid var(--line);padding:8px 10px;font-size:12px}}td{{padding:8px 10px;border-bottom:1px solid #1d2129}}.kw{{font-family:Consolas,monospace;color:#dfe6ee}}.num{{text-align:right;color:var(--accent);font-weight:600}}.dash{{color:#4a5160}}.tag{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;background:#22303a;color:var(--blu)}}.tag.p{{background:#2c2433;color:#c79bff}}.tag.n{{background:#2a2014;color:#caa46a}}.foot{{color:var(--mut);font-size:12.5px;border-top:1px dashed var(--line);margin-top:28px;padding-top:14px}}ol li,ul li{{margin:8px 0}}</style></head><body><div class="wrap">
<header><div class="kicker">亚马逊万词计划 · LISTING 埋词审计</div><h1>{esc(product)} · {esc(site)} 站</h1><div class="meta">ASIN {esc(asin)} · {esc(store)} · 词库 {total} 词 · 自动生成(L2)</div></header>
{hb}
<h2>📊 三个关键数（别混）</h2><div class="stat-row"><div class="stat"><div class="n">{total}</div><div class="l">候选池(词库总词)<br>含待校验噪音</div></div><div class="stat"><div class="n">{len(rk)}</div><div class="l">✅ 已收录(有自然排名)<br>占候选 {rkp}%</div></div><div class="stat"><div class="n">{embedded}</div><div class="l">已埋(埋进文案)<br>占候选 {ep}%</div></div><div class="stat"><div class="n">{fit}</div><div class="l">合适词(剔噪后)<br>直写{len(embeddable)}+UGC{len(ugc)}</div></div></div>
<h2>🎯 已收录 {len(rk)} 词 · 收录质量分层</h2><div class="card"><div class="tier"><div class="tierbox t-good"><div class="tn">{len(p1)}</div><div class="tl">首页(≤16名)</div></div><div class="tierbox t-mid"><div class="tn">{len(p23)}</div><div class="tl">2-3页(17-48)</div></div><div class="tierbox t-bad"><div class="tn">{len(deep)}</div><div class="tl">靠后(&gt;48)</div></div></div><div style="color:var(--mut);font-size:13px;margin-top:10px">收录≠首页：{len(rk)} 个有排名里只 {len(p1)} 个首页，{len(deep)} 个在第3页后。万词要把它们往首页推 + 把合适漏埋词推进收录。</div></div>
<h2>✅ 改进意见</h2><div class="card"><ol><li><strong>{'先补全文案+上架可售' if (be or de or se or not buy) else '补齐缺失埋词层'}</strong>：空层补全(可本地化美国站文案),确认库存价格变 BUYABLE。</li><li><strong>后台ST立即填</strong>：最易补,先塞高价值漏埋词。</li><li><strong>敏感词走UGC</strong>：nintendo 描述里 compatible with 埋1处;IP/竞品靠 Review/QA。</li><li><strong>机型兼容词可直写</strong>(本品支持的机型)。</li></ol></div>
<h2>📌 高价值漏埋词 Top20 · 可直写补埋</h2><div style="color:var(--mut);font-size:13px">只留含本品类锚点+机型兼容词;游戏/别平台/跨品类/不兼容机型已排除。按月搜量+出单排序。</div>
<table><thead><tr><th>关键词</th><th>矩阵</th><th class="num">月搜量</th><th class="num">已出单</th></tr></thead><tbody>{miss_h}</tbody></table>
<div class="callout c-yel"><strong style="color:var(--yel)">⚠️ 走 UGC 不直写的敏感词</strong>(漏埋但靠 Review/QA 收录,别塞ST/五点)<ul style="margin-bottom:0">{ugc_h}</ul></div>
<h2>🗑 候选池噪音（运营在表1「矩阵」校验）</h2><div style="color:var(--mut);font-size:13px">不含本品类锚点(游戏/别平台/跨品类/不兼容机型/价格二手),不算合适词,不必埋：</div>
<table><thead><tr><th>关键词</th><th>判定</th><th class="num">月搜量</th></tr></thead><tbody>{nz_h}</tbody></table>
<div class="foot">领星 product/search 拉真实文案 → 词库逐词比对 + 自然排名收录分层 + 品类锚点白名单净化。矩阵为系统初分,运营校验。本服务自动生成。</div></div></body></html>"""

# ───────────────── 表2/3/5/6 填充 ─────────────────
COLORS=["red","pink","blue","black","white","green","purple","yellow","gray","grey","clear","orange","mint","lavender"]
PRICE=["used","refurbished","renewed","deals","cheap","clearance","second hand","segunda mano","usado","reacondicionado"]
CROSS={"dock":["controller","case","carrying case","screen protector","grip","skin","joycon","tempered glass","wired controller"],"controller":["case","carrying case","cover","skin","dock","docking station","wall mount","screen protector","tempered glass","grip tape"],"case":["controller","dock","docking station","charger","grip","joycon","screen protector","wall mount"]}
def P(name,atype,match,kws,bid,budget,acos,stage,reason): return {"计划名":name,"广告类型":atype,"匹配类型":match,"包含关键词":kws,"建议bid":bid,"建议日预算":budget,"目标ACoS":acos,"状态":"待审","阶段":stage,"开广告理由":reason,"已出单":0}
def ads_tpl(cat):
    if cat=="controller": return [P("SP-Auto-手柄捡词","SP-Auto自动","自动(4匹配)","系统自动匹配","$0.45","$20","30%","P1","起量+挖搜索词;低bid捡漏"),P("SP-Exact-核心手柄大词","SP手动Exact","Exact","switch 2 controller | nintendo switch 2 controller | switch 2 pro controller","$1.0","$25","28%","P1","核心词Exact卡位"),P("SP-Exact-中词扩量","SP手动Exact","Exact","hall effect controller | switch controller wireless","$0.8","$20","30%","P2","中词扩量"),P("SP-Broad-手柄长尾","SP手动Broad","Broad","switch 2 controller with paddles | turbo controller switch","$0.5","$15","32%","P1","Broad发长尾(精准否锁大词)"),P("SP-Exact-卖点簇","SP手动Exact","Exact","hall effect joystick | back paddle controller | turbo | rgb controller","$0.7","$12","30%","P2","霍尔/背键/连发/RGB"),P("SD-竞品手柄定投","SD商品定投","ASIN定投","8bitdo/GameSir/NYXI 竞品ASIN","$0.6","$12","32%","P2","SD打竞品详情页"),P("SBV-手柄品牌簇","SBV视频","Exact","switch 2 controller","$1.0","$15","30%","P2","视频展示霍尔+握感"),P("SP-Exact-礼品词","SP手动Exact","Exact","gifts for gamers | switch gifts","$0.6","$10","32%","Q4","Q4礼品季")]
    if cat=="case": return [P("SP-Auto-卡盒捡词","SP-Auto自动","自动(4匹配)","系统自动匹配","$0.40","$15","30%","P1","起量+挖词"),P("SP-Exact-核心卡盒大词","SP手动Exact","Exact","switch 2 case | nintendo switch 2 case | switch 2 carrying case","$0.8","$20","28%","P1","核心词Exact卡位"),P("SP-Exact-中词扩量","SP手动Exact","Exact","switch 2 storage case | hard shell switch case | switch game holder","$0.6","$15","30%","P2","中词扩量"),P("SP-Broad-卡盒长尾","SP手动Broad","Broad","switch 2 travel case | slim case switch","$0.45","$12","32%","P1","Broad发长尾"),P("SP-Exact-卖点簇","SP手动Exact","Exact","hard shell switch 2 case | switch case 10 game","$0.55","$10","30%","P2","硬壳/卡槽/便携"),P("SD-竞品卡盒定投","SD商品定投","ASIN定投","tomtoc/Belkin 竞品ASIN","$0.5","$10","32%","P2","SD打竞品卡盒"),P("SBV-卡盒品牌簇","SBV视频","Exact","switch 2 case","$0.8","$12","30%","P2","展示卡槽+材质"),P("SP-Exact-礼品词","SP手动Exact","Exact","gifts for gamers | switch gifts","$0.5","$10","32%","Q4","Q4礼品季")]
    return [P("SP-Auto-dock捡词","SP-Auto自动","自动(4匹配)","系统自动匹配","$0.45","$20","28%","P1","起量+挖词"),P("SP-Exact-核心dock大词","SP手动Exact","Exact","switch 2 dock | nintendo switch 2 dock | switch 2 docking station","$1.2","$25","25%","P1","核心词Exact卡位"),P("SP-Exact-中词扩量","SP手动Exact","Exact","switch dock | switch 2 tv dock | switch 2 charging dock","$0.9","$20","28%","P2","中词扩量"),P("SP-Broad-dock长尾","SP手动Broad","Broad","switch 2 portable dock | switch oled dock","$0.5","$15","30%","P1","Broad发长尾"),P("SP-Exact-卖点簇","SP手动Exact","Exact","switch 2 dock with fan | switch 2 4k dock","$0.8","$12","28%","P2","散热/4K/充电"),P("SD-竞品dock定投","SD商品定投","ASIN定投","JSAUX/Genki 竞品ASIN","$0.6","$12","30%","P2","SD打竞品dock"),P("SBV-dock品牌簇","SBV视频","Exact","switch 2 dock","$1.0","$15","28%","P2","展示散热+4K"),P("SP-Exact-礼品词","SP手动Exact","Exact","gifts for gamers | switch gifts","$0.6","$10","32%","Q4","Q4礼品季")]
def fill_234(app,t1,t2,t3,t5,t6,L,cat,site):
    supp=supported_machines(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"])
    tt=set(toks(L["title"])); bt=set()
    for b in L["bullets"]: bt|=set(toks(b))
    dt=set(toks(L["desc"])); st=set(toks(L["st"])); front=tt|bt|dt
    def cov(kw,s): k=toks(kw); return bool(k) and all(w in s for w in k)
    rows=[r["fields"] for r in lall(app,t1) if r["fields"].get("站点")==site]
    ensure_site(app,t2); clear(app,t2,lambda f:f.get("站点")==site)
    t2r=[]
    for f in rows:
        kw=ext(f.get("关键词")); mx=f.get("矩阵")
        if mx not in ("意图词","品牌词-平台","品牌词-竞品","IP词"): continue
        inT=cov(kw,tt);inB=cov(kw,bt);inD=cov(kw,dt);inS=cov(kw,st);fr=cov(kw,front)
        ch=("直写前台(标题/五点/描述/后台ST)" if mx=="意图词" else ("后台ST已埋(for形式)+UGC" if (mx=="品牌词-平台" and "nintendo" in kw.lower()) else ("直写前台(标题/五点/描述/后台ST)" if mx=="品牌词-平台" else ("UGC评论QA+广告可打" if mx=="品牌词-竞品" else "UGC评论QA"))))
        if fr or inS: status="已埋" if fr else "已埋(ST)"
        elif mx in ("意图词","品牌词-平台"): status="待埋(补描述)" if qualify_embed(kw,cat,supp) else "不埋"
        else: status="UGC待引导"
        t2r.append({"关键词":kw,"站点":site,"矩阵":mx,"埋词渠道":ch,"标题已埋":inT,"五点已埋":inB,"描述已埋":inD,"后台ST已埋":inS,"前台已覆盖":fr,"埋词状态":status})
    n2=batch(app,t2,t2r)
    n5=0
    if not lall(app,t5):
        n5=batch(app,t5,[{"阶段":"P1 (0-30d)","阶段目标":"低SPR小词冲首页+核心品类词建联","关键KPI":"核心词进首页;Auto挖词反哺","农村是否生效":"观察中","下阶段触发条件":"核心词稳定P1"},{"阶段":"P2 (30-60d)","阶段目标":"大词排名爬升+中词扩量+补埋","关键KPI":"大词进前2页;簇收录率>50%","农村是否生效":"观察中","下阶段触发条件":"大词进前2页+ACoS可控"},{"阶段":"P3 (60d+)","阶段目标":"核心词进前10转防守+SD打竞品","关键KPI":"核心词稳定前10","农村是否生效":"观察中","下阶段触发条件":"前10稳定2周"}])
    clear(app,t3); n3=batch(app,t3,ads_tpl(cat))
    clear(app,t6); out=[]; seen=set()
    def add(w,way,c,note):
        wl=w.strip().lower()
        if wl and wl not in seen: seen.add(wl); out.append({"否定词":w.strip(),"否定方式":way,"类别":c,"状态":"待添加","应用范围":"全广告活动","备注":note})
    for w in ["switch","nintendo switch","switch 2","nintendo switch 2","nintendo","switch oled","nintendo switch oled","switch lite","steam deck","steamdeck"]: add(w,"精准否定","大词/品牌/泛词","裸平台大词:只否精确,留Broad发长尾")
    for c in COLORS: add(c,"词组否定","颜色词","本品单色,其余颜色整片否(运营留自己色)")
    for w in PRICE: add(w,"词组否定","其他(配件/平台)","价格/二手意图")
    for w in CROSS.get(cat,[]): add(w,"词组否定","其他(配件/平台)","别品类配件,整片屏蔽")
    for w in ["xbox","ps5","ps4","ps3","playstation","dualsense","pc","steam controller","android"]: add(w,"词组否定","其他(配件/平台)","别平台,整片屏蔽")
    for f in rows:
        kw=ext(f.get("关键词")); mx=f.get("矩阵"); kl=kw.lower()
        if not kw: continue
        if is_comp(kl): add(kw,"精准否定","大词/品牌/泛词","竞品品牌,精准否")
        elif is_pure_console(kl): add(kw,"精准否定","大词/品牌/泛词","游戏/主机/捆绑搜索,精准否")
        elif incompatible_machine(kl,supp): add(kw,"词组否定","其他(配件/平台)","不兼容该机型,整片屏蔽")
        elif mx=="IP词" or is_ip(kl): add(kw,"词组否定","IP词","未授权IP,靠UGC")
        if len(out)>=90: break
    n6=batch(app,t6,out)
    return n2,n3,n5,n6

def lookup_sku(sid,asin):
    off=0
    while off<2500:
        r=lx("/erp/sc/data/mws/listing",{"sid":sid,"length":50,"offset":off})
        data=r.get("data") or []
        if not data: break
        for it in data:
            if it.get("asin")==asin and it.get("seller_sku"): return it["seller_sku"]
        if len(data)<50: break
        off+=len(data); time.sleep(0.2)
    return None

DOMAIN={"US":1,"UK":2,"DE":3,"FR":4,"ES":8,"IT":9,"CA":6,"MX":10,"JP":7,"AU":12}
# ───────────────── 主编排 ─────────────────
def process(rid):
    rec=api("GET",f"/bitable/v1/apps/{REG_APP}/tables/{APPLY_TB}/records/{rid}")["data"]["record"]["fields"]
    g=lambda k: ext(rec.get(k))
    product=g("产品"); site=rec.get("站点"); region=rec.get("区域"); asin=g("ASIN"); cat=rec.get("品类")
    op=g("负责运营"); sid=int(ext(rec.get("店铺sid")) or 0); sku=g("seller_sku(可空自动查)")
    reuse=g("复用App_token(可空,空=自动新建)"); domain=int(ext(rec.get("Sorftime_domain")) or DOMAIN.get(site,0))
    layout=rec.get("报表布局") or ""; lin="林明坚式" in layout
    store=g("店铺名") or ""
    upd(REG_APP,APPLY_TB,rid,{"状态":"处理中"})
    log=[]
    try:
        # 1. 下载+解压报表
        atts=rec.get("报表压缩包(zip)") or []
        tmp=tempfile.mkdtemp(prefix="wanci_")
        for a in atts:
            dest=os.path.join(tmp,a.get("name","r.zip"))
            download_media(a["file_token"],dest)
            if dest.lower().endswith(".zip"):
                with zipfile.ZipFile(dest) as z: z.extractall(tmp)
        xls=glob.glob(os.path.join(tmp,"**","*.xlsx"),recursive=True)+glob.glob(os.path.join(tmp,"**","*.xls"),recursive=True)
        xls=[x for x in xls if "~$" not in os.path.basename(x)]
        files={"self":[],"comp":[],"mining":[],"aba":[],"sp_amz":[],"sp_ss":[],"biz":[]}
        for x in xls:
            c=classify_report(os.path.basename(x),asin); files.setdefault(c,[]).append(x)
        log.append(f"报表 {len(xls)} 文件: self{len(files['self'])} comp{len(files['comp'])} mining{len(files['mining'])} aba{len(files['aba'])} sp_amz{len(files['sp_amz'])} sp_ss{len(files['sp_ss'])}")
        # 2. App
        if reuse:
            app=reuse; t=api("GET",f"/bitable/v1/apps/{app}/tables?page_size=100")["data"]["items"]
            tm={x["name"]:x["table_id"] for x in t}
            T1=tm["表1·关键词词库"];T2=tm["表2·Listing埋词审计"];T3=tm["表3·广告计划建议"];T4=tm["表4·排名收录追踪"];T5=tm["表5·阶段目标与审计"];T6=tm["表6·否定词库"]
        else:
            app,tm=clone_app(f"亚马逊万词作战台·{product}-{region}")
            T1=tm["表1·关键词词库"];T2=tm["表2·Listing埋词审计"];T3=tm["表3·广告计划建议"];T4=tm["表4·排名收录追踪"];T5=tm["表5·阶段目标与审计"];T6=tm["表6·否定词库"]
        # 3. 导词库(幂等)
        if not any(r["fields"].get("站点")==site for r in lall(app,T1)):
            ensure_t1_extra(app,T1)
            t1d,t4d=import_keywords(files,site,asin)
            batch(app,T1,t1d); batch(app,T4,t4d) if t4d else 0
            log.append(f"导词库 表1={len(t1d)} 表4={len(t4d)}")
        else: log.append("表1已有该站点,跳过导入")
        # 4. 登记总台(幂等)
        exist={(ext(x["fields"].get("ASIN")),x["fields"].get("站点")) for x in lall(REG_APP,REG_TB)}
        if (asin,site) not in exist:
            api("POST",f"/bitable/v1/apps/{REG_APP}/tables/{REG_TB}/records",{"fields":{"产品":product,"站点":site,"ASIN":asin,"父ASIN":g("父ASIN"),"Sorftime_domain":domain,"作战台App_token":app,"词库表id":T1,"表4排名收录id":T4,"rank基础表token":RANK_BASE,"状态":"筹备","数据源":rec.get("数据源") or "人手卖家精灵","区域":region,"备注":"L2自动上线"}})
            log.append("已登记总台")
        # 5. seller_sku + listing
        if not sku: sku=lookup_sku(sid,asin)
        html_url=""
        if sku:
            lr=lx("/listing/publish/openapi/amazon/product/search",{"store_id":sid,"skus":[sku]})
            L=load_listing(lr)
            html=make_html(product,site,asin,store or f"sid{sid}",L,[r for r in lall(app,T1) if r["fields"].get("站点")==site],cat)
            hp=os.path.join(tmp,f"audit_{asin}_{site}.html"); io.open(hp,"w",encoding="utf-8").write(html)
            n2,n3,n5,n6=fill_234(app,T1,T2,T3,T5,T6,L,cat,site)
            log.append(f"填表 表2={n2} 表3={n3} 表5={n5} 表6={n6}")
            oid=OP_OID.get(op)
            if oid:
                fk=upload_file(hp,f"{product}-{site}-listing审计.html")
                im_text(oid,f"【万词·Listing审计】{product} {site} 作战台已建好+审计报告(HTML,浏览器开)请查收。作战台6表已填。")
                im_file(oid,fk); log.append(f"已发运营 {op}")
        else: log.append("⚠️ 查不到seller_sku,跳过审计(已建词库+登记)")
        appurl=f"https://u1wpma3xuhr.feishu.cn/base/{app}"
        upd(REG_APP,APPLY_TB,rid,{"状态":"已完成","处理结果":" | ".join(log)[:900],"作战台链接":{"link":appurl,"text":product+"-"+region}})
        return {"ok":True,"app":app,"log":log}
    except Exception as e:
        import traceback; tb=traceback.format_exc()[-800:]
        upd(REG_APP,APPLY_TB,rid,{"状态":"失败","处理结果":(" | ".join(log)+" | ERR "+str(e))[:900]})
        return {"ok":False,"err":str(e),"tb":tb,"log":log}

app=FastAPI()
@app.get("/")
def root(): return {"service":"wanci-onboard","ok":True}
@app.get("/selftest")
def selftest():
    try:
        n=len(lall(REG_APP,APPLY_TB)); return {"ok":True,"apply_rows":n,"lx":lx("/erp/sc/data/seller/lists",{}).get("code")}
    except Exception as e: return {"ok":False,"err":str(e)}
@app.post("/onboard")
async def onboard(req:Request):
    if AUTH_TOKEN and req.headers.get("authorization","")!="Bearer "+AUTH_TOKEN: return {"ok":False,"err":"unauthorized"}
    body=await req.json(); rid=body.get("record_id")
    if not rid: return {"ok":False,"err":"record_id required"}
    threading.Thread(target=process,args=(rid,),daemon=True).start()  # 后台跑绕开网关超时
    return {"ok":True,"msg":"processing","record_id":rid}
