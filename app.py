# -*- coding: utf-8 -*-
"""万词上线自动化服务 (L2). 飞书「万词上线申请」表 → n8n 触发 → 本服务全自动:
下载报表zip → 建/复用作战台 → 导词库(表1+表4) → 登记总台 → 拉listing文案 → 埋词审计HTML
→ 填表2/3/5/6 → 发对应运营 → 回写状态。 密钥全走 env(public repo 不内联)。"""
import os, io, re, json, time, uuid, zipfile, tempfile, glob, threading, urllib.request, datetime
from fastapi import FastAPI, Request

FEISHU_APP_ID=os.environ["FEISHU_APP_ID"]; FEISHU_APP_SECRET=os.environ["FEISHU_APP_SECRET"]
PROXY=os.environ.get("LX_PROXY","https://frankiepan501.zeabur.app/webhook/lingxing-proxy")
PROXY_TOK=os.environ["LX_PROXY_TOKEN"]
TEMPLATE_APP=os.environ.get("WANCI_TEMPLATE_APP","FcycbOqACaimScsAMlCcSuDznJb")  # 食人花dock-北美 6表模板源
REG_APP=os.environ.get("WANCI_REG_APP","W8LPboJSMaVqlwsizQ8cPVDIn2c")
REG_TB=os.environ.get("WANCI_REG_TB","tbl2g78DcPnxWNwO")
APPLY_TB=os.environ.get("WANCI_APPLY_TB","tblPXS4uO8lK9p5g")
RANK_BASE=os.environ.get("WANCI_RANK_BASE","EEKNbZ8b8aqv6msOaTscotBDn5f")
SNAP_TB=os.environ.get("WANCI_SNAP_TB","tbl3OipVxS8wyjKk")  # 万词周快照表(总台App内)
TARGET_ACOS=float(os.environ.get("WANCI_TARGET_ACOS","35"))  # 目标ACoS%(默认35,低于食人花dock~40盈亏平衡;判提预算/优化的阈值)
def ad_verdict(acos,sal):
    """广告表现判定(供"是否值得提预算"):盈利(ACoS≤target)=健康;否则需优化。"""
    if sal<=0: return "无成交"
    return "健康" if (acos and acos<=TARGET_ACOS) else "ACoS偏高"
AUTH_TOKEN=os.environ.get("ONBOARD_TOKEN","")
FRANKIE_OID="ou_629ce01f4bc31de078e10fcb038dbf78"
BASE="https://open.feishu.cn/open-apis"
OP_OID={  # 负责运营 → 聪哥1号 open_id (路由HTML/卡片)
 "陈翔宇":"ou_9c322382284a7a6672a091b9f4c0a551","林明坚":"ou_35aa6883c0598bac5c7e06fcb06f7c4d",
 "余培霓":"ou_40ff10b05fc358f88c5674f053665551","潘志聪":"ou_629ce01f4bc31de078e10fcb038dbf78",
 "黄奕纯":"ou_1b981067ce8edfd82af7c70c109310e4"}

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
        out+=(d.get("items") or [])
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
HARD_PLATFORM=["xbox","ps5","ps4","ps3","play 4","play 5","play station","playstation","dualsense","steam deck","steamdeck"," steam","vr glasses","3ds","psp","nintendo ds"," ds ","dsi","gamecube","game cube","n64","nintendo 64"," 64 ","ps portal","portal","yoto","raspberry"]  # 协议/设备不兼容,永剔
SOFT_PLATFORM=["pc","windows","android"," ios ","iphone","ipad"," phone","mobile","movil","móvil","celular","tablet"]  # 手柄常兼容; listing声明支持才放行(R4 林明坚)
OTHER_PLATFORM=HARD_PLATFORM+SOFT_PLATFORM  # 兼容旧引用:无soft上下文时全排
def is_hard_platform(k): kl=" "+k.lower()+" "; return any(n in kl for n in HARD_PLATFORM)
def soft_platform_hit(k): kl=" "+k.lower()+" "; return any(n in kl for n in SOFT_PLATFORM)
def supports_soft(text): t=" "+text.lower()+" "; return any(n in t for n in [" pc ","windows","android"," ios ","iphone","mobile"," phone"])
PURE_CONSOLE=["console","consola","konsole","bundle"," games","switch games","spiele "]
COMP_BRANDS=["8bitdo","8 bit do","8bit do","gamesir","razer","gulikit","nyxi","mobapad","hori ","ipega","flydigi","binbok","powera","power a","pxn","pdp ","nyko","iine","kingkong","easysmx","voyee","nitro deck","jsaux","genki","antank","belkin","tomtoc","spigen","dbrand","mooroer","fintie","procase","orzly","skull & co","skull and co","geekshare","playvital","geekria","mumba","younik","hyperkin"]
COMP_DISPLAY={"8bitdo":"8BitDo","8 bit do":"8BitDo","8bit do":"8BitDo","gamesir":"GameSir","nyxi":"NYXI","powera":"PowerA","power a":"PowerA","pdp":"PDP","hori":"Hori","gulikit":"GuliKit","mobapad":"Mobapad","binbok":"Binbok","nitro deck":"Nitro Deck","ipega":"iPega","flydigi":"Flydigi","easysmx":"EasySMX","voyee":"VOYEE","pxn":"PXN","hyperkin":"Hyperkin","nyko":"Nyko","iine":"IINE","jsaux":"JSAUX","genki":"Genki","tomtoc":"tomtoc","belkin":"Belkin","spigen":"Spigen","razer":"Razer"}
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
TRADEMARK=["nintendo"]  # 主机商标(sony/microsoft 已在 OTHER_PLATFORM);走UGC/仅for-compatible措辞,不直写标题五点
def is_trademark(k): kl=" "+k.lower()+" "; return any(t in kl for t in TRADEMARK)
MISSPELL=[r'\bswich\b',r'\bswithc\b',r'\bswtich\b','switch2','nintendoswitch',r'\bprocontroller\b',r'\bninendo\b',r'\bnintedo\b',r'\bnintndo\b',r'\bcontoller\b',r'\bcontroler\b',r'\bcontorller\b',r'\bgamepd\b']
def is_misspell(k):
    kl=k.lower()
    return any(re.search(p,kl) for p in MISSPELL)
EN_SITES={"US","CA","UK","AU"}  # 英语关键词站; 其余(MX/DE/FR/ES/IT/JP)广告计划走本地化骨架
def qualify_embed(kw,cat,supp,soft=False):
    k=kw.lower()
    if is_trademark(k) or is_misspell(k): return False  # 商标走UGC / 拼写变体只投广告不写listing
    if is_hard_platform(k): return False  # xbox/ps/steam 永剔
    if soft_platform_hit(k) and not soft: return False  # pc/手机: listing没声明支持则剔(R4)
    if is_pure_console(k) or is_ip(k): return False
    if incompatible_machine(k,supp): return False
    if is_machine_compat(k): return True
    return any(a in k for a in CAT_ANCHORS.get(cat,CAT_ANCHORS["dock"]))
def agg_roots(items):
    """#2 按差异化词根(去机型词 switch/2/nintendo/oled..)聚合,同根只留最高vol代表+累加vol。
    items: [{kw,vol,ord,...}](已按vol降序)。杀「132个switch 2变体逐个列漏埋」。"""
    seen={}; out=[]
    for r in items:
        key=frozenset(t for t in toks(r["kw"]) if t not in MACHINE_TOK)
        if not key: continue  # 纯机型词无差异化词根
        if key in seen: seen[key]["vol"]=seen[key].get("vol",0)+r.get("vol",0); continue
        rr=dict(r); seen[key]=rr; out.append(rr)
    return out

def load_listing(d):
    info=(d.get("data") or [{}])[0].get("info",{}); at=info.get("attributes",{}) or {}
    def g(k):
        v=at.get(k)
        if isinstance(v,list): return [(x.get("value") if isinstance(x,dict) else x) for x in v]
        return v
    def s1(v): return " ".join(str(x) for x in v if x) if isinstance(v,list) else (str(v) if v is not None else "")
    bl=g("bullet_point") or []
    if not isinstance(bl,list): bl=[bl]
    su=info.get("summaries",[{}]); su0=su[0] if su else {}
    authored=bool(s1(g("item_name")) or g("bullet_point") or g("product_description"))  # 我方是否自建文案(非跟卖/offer-only)
    title=s1(g("item_name")) or (su0.get("itemName") or "")  # 标题兜底 summaries.itemName(跟卖记录文案只在summaries)
    return {"title":title,"bullets":[b for b in bl if b],"desc":s1(g("product_description")),
            "st":s1(g("generic_keyword")),"status":su0.get("status",[]),
            "authored":authored,"has_record":bool(d.get("data"))}

# 列位 & 报表解析 (与 import_seller_sprite 一致)
REV={"kw":0,"nat":9,"ad":12,"vol":16,"spr":17,"buy":20,"demand":24,"ppc":28,"top10":30}
MIN={"kw":0,"vol":6,"buy":8,"spr":11,"demand":14,"ppc":18,"top10":33}
ABA={"kw":0,"vol":2,"ppc":7,"spr":11,"top10":18}
TERM_HDRS=["用户搜索词","客户搜索词","customer search term","search term","搜索词"]
ORDER_HDRS=["广告订单","7 day total orders (#)","7天总订单数(#)","total orders","订单数","订单量"]
IPg=["zelda","mario","pokemon","pikachu","kirby","minecraft","rosalina","yoshi","splatoon","metroid","sonic","dave","diver","luminex","animal crossing"]
PRICEg=["used","refurbished","renewed","deals","cheap","clearance","segunda mano","usado","reacondicionado","barato","oferta"]
COMPg=["8bitdo","gamesir","nyxi","mobapad","jsaux","genki","antank","binbok","ponkor","hori","oivo","gulikit","kdd","younik","natuk","jingmai","fastsnail","nexigo","powera","power a","pdp","pxn"]
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
def classify_report(bn,self_asin,lin):
    if bn.startswith("ReverseASIN-"): return "self" if self_asin in bn else "comp"
    if bn.startswith("KeywordMining-"): return "mining"
    if bn.startswith("ABAKeywordTrend-"): return "aba"
    if bn.startswith("FUNLAB-") or bn.startswith("Fanlepu-"): return "sp_ss"
    if bn.startswith("BusinessReport"): return "biz"
    if "Search_term_report" in bn: return "sp_amz"
    return "sp_amz" if lin else "self"  # 标准布局: 余下(根xlsx)=自家反查; 林明坚式: =亚马逊原生广告报表
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
    supp=supported_machines(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]); soft=supports_soft(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]+" "+L["st"])
    tt=set(toks(L["title"])); bt=set()
    for b in L["bullets"]: bt|=set(toks(b))
    dt=set(toks(L["desc"])); st=set(toks(L["st"])); front=tt|bt|dt
    def cov(kw,s): k=toks(kw); return bool(k) and all(w in s for w in k)
    R=[]
    for r in rows:
        f=r["fields"]; kw=ext(f.get("关键词"))
        R.append({"kw":kw,"mx":f.get("矩阵"),"vol":float(ext(f.get("月搜索量")) or 0),"ord":float(ext(f.get("已出单单量")) or 0),
                  "rank":float(ext(f.get("我方自然排名")) or 0),"front":cov(kw,front),"instr":cov(kw,st),"qual":qualify_embed(kw,cat,supp,soft)})
    total=len(R); embedded=sum(1 for r in R if r["front"] or r["instr"])
    rk=[r for r in R if r["rank"]>0]; p1=[r for r in rk if r["rank"]<=16]; p23=[r for r in rk if 16<r["rank"]<=48]; deep=[r for r in rk if r["rank"]>48]
    sens=lambda r: r["mx"] in ("IP词","品牌词-竞品") or is_ip(r["kw"]) or is_comp(r["kw"]) or is_trademark(r["kw"])
    ugc=[r for r in R if sens(r)]; embeddable=[r for r in R if r["qual"] and not sens(r)]; noise=[r for r in R if (not r["qual"]) and not sens(r)]
    fit=len(embeddable)+len(ugc)
    miss=agg_roots(sorted([r for r in embeddable if not(r["front"] or r["instr"])],key=lambda r:-(r["vol"]+r["ord"]*5000)))
    missu=sorted([r for r in ugc if not(r["front"] or r["instr"])],key=lambda r:-(r["vol"]+r["ord"]*5000))
    nz=sorted(noise,key=lambda r:-r["vol"])
    softw=sorted([r for r in R if soft_platform_hit(r["kw"]) and not is_hard_platform(r["kw"]) and not is_ip(r["kw"]) and not is_comp(r["kw"])],key=lambda r:-r["vol"])
    soft_hint=("" if (soft or not softw) else f"<div class=\"callout c-yel\"><strong style=\"color:var(--yel)\">💡 多平台机会（{len(softw)} 个 PC/手机词暂被剔）</strong>：本品 listing 未声明 PC/手机支持，故按别平台剔除。若产品实际支持 PC/Android（多数 Switch 手柄支持），listing 补一句「Compatible with PC / Android / iOS」即可解锁这些词进埋词+广告。Top：{' / '.join(esc(r['kw']) for r in softw[:8])}</div>")
    be=len(L["bullets"])==0; de=not L["desc"].strip(); se=not L["st"].strip(); buy="BUYABLE" in (L["status"] or [])
    notext=(not L["title"].strip()) and be and de and se
    rkp=round(100.0*len(rk)/max(total,1)); ep=round(100.0*embedded/max(total,1))
    def trow(r):
        v="{:,}".format(int(r["vol"])) if r["vol"] else "<span class='dash'>—</span>"; o=str(int(r["ord"])) if r["ord"] else "<span class='dash'>—</span>"
        return f"<tr><td class='kw'>{esc(r['kw'])}</td><td><span class='tag'>{esc(r['mx'])}</span></td><td class='num'>{v}</td><td class='num'>{o}</td></tr>"
    miss_h="\n".join(trow(r) for r in miss[:20])
    ugc_h="\n".join(f"<li><span class='kw'>{esc(r['kw'])}</span> <span class='tag p'>{esc(r['mx'])}</span> 出单 {int(r['ord']) if r['ord'] else 0} → 引导 Review/QA</li>" for r in missu[:12]) or "<li>（无）</li>"
    nlabel=lambda r:("拼写变体→广告可投·勿写listing" if is_misspell(r["kw"]) else esc(r["mx"])+"→疑噪")
    nz_h="\n".join(f"<tr><td class='kw' style='color:#8b94a3'>{esc(r['kw'])}</td><td><span class='tag n'>{nlabel(r)}</span></td><td class='num'>{('{:,}'.format(int(r['vol']))) if r['vol'] else '—'}</td></tr>" for r in nz[:15]) or "<tr><td colspan=3 style='color:#6b7280'>（无）</td></tr>"
    if not L.get("has_record",True):
        hb=f"""<div class="callout c-yel"><h2 style="margin-top:0">🟡 跟卖 / 本店无自建 listing</h2><ul><li>该 seller_sku 在本店<strong>无 listing 记录</strong>（纯跟卖他人 ASIN 的 offer，或 sku 填错）。</li><li>无法编辑被跟卖 listing 的文案 → <strong>埋词需先自建独立 listing</strong>。下方「已收录」来自反查仍有效。</li></ul></div>"""
    elif not L.get("authored",True):
        hb=f"""<div class="callout c-yel"><h2 style="margin-top:0">🟡 跟卖 / 未自建文案</h2><ul><li>本店只挂 offer 匹配到已有 ASIN，标题《{esc(L['title'][:60])}》来自<strong>被跟卖 listing</strong>，我方<strong>未自建五点/描述/后台ST</strong>（attributes 无 item_name/bullet_point）。</li><li>→ 无法埋词；要埋词须<strong>自建独立 listing</strong>（或在拥有该 listing 文案的店铺操作）。非领星同步问题。</li></ul></div>"""
    elif notext:
        hb=f"""<div class="callout c-red"><h2 style="margin-top:0">🔴 listing 文案全空（标题/五点/描述/ST）</h2><ul><li>状态 <strong>{esc('/'.join(L['status']) or '未知')}</strong>。请运营核实后台 listing 是否建全。下方「已收录」仍有效。</li></ul></div>"""
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
<h2>📌 高价值漏埋词根 Top20 · 可直写补埋</h2><div style="color:var(--mut);font-size:13px">已按<b>差异化词根聚合</b>(switch/switch 2/nintendo 等机型变体合并,只提示真正缺的卖点词根,不堆词);商标/竞品/IP/游戏/别平台/拼写变体已排除。月搜量为同根累加。</div>
<table><thead><tr><th>关键词</th><th>矩阵</th><th class="num">月搜量</th><th class="num">已出单</th></tr></thead><tbody>{miss_h}</tbody></table>
<div class="callout c-yel"><strong style="color:var(--yel)">⚠️ 走 UGC 不直写的敏感词</strong>(漏埋但靠 Review/QA 收录,别塞ST/五点)<ul style="margin-bottom:0">{ugc_h}</ul></div>
{soft_hint}
<h2>🗑 候选池噪音（运营在表1「矩阵」校验）</h2><div style="color:var(--mut);font-size:13px">不含本品类锚点(游戏/别平台/跨品类/不兼容机型/价格二手),不算合适词,不必埋：</div>
<table><thead><tr><th>关键词</th><th>判定</th><th class="num">月搜量</th></tr></thead><tbody>{nz_h}</tbody></table>
<div class="foot">领星 product/search 拉真实文案 → 词库逐词比对 + 自然排名收录分层 + 品类锚点白名单净化。矩阵为系统初分,运营校验。本服务自动生成。</div></div></body></html>"""

# ───────────────── 表2/3/5/6 填充 ─────────────────
COLORS=["red","pink","blue","black","white","green","purple","yellow","gray","grey","clear","orange","mint","lavender"]
PRICE=["used","refurbished","renewed","deals","cheap","clearance","second hand","segunda mano","usado","reacondicionado"]
CROSS={"dock":["controller","case","carrying case","screen protector","grip","skin","joycon","tempered glass","wired controller"],"controller":["case","carrying case","cover","skin","dock","docking station","wall mount","screen protector","tempered glass","grip tape"],"case":["controller","dock","docking station","charger","grip","joycon","screen protector","wall mount"]}
def P(name,atype,match,kws,bid,budget,acos,stage,reason): return {"计划名":name,"广告类型":atype,"匹配类型":match,"包含关键词":kws,"建议bid":bid,"建议日预算":budget,"目标ACoS":acos,"状态":"待审","阶段":stage,"开广告理由":reason,"已出单":0}
def local_comp_brands(rows,topn=8):
    """#翔宇: SD竞品定投取本站词库真实竞品品牌(本地市场)非美国硬编。
    按「变体出现数」为主(防月搜量缺失埋没 PowerA/PDP 等少变体但市场突出品牌),月搜量为次。"""
    from collections import Counter
    c=Counter()
    for f in rows:
        kw=" "+ext(f.get("关键词")).lower()+" "; vol=float(ext(f.get("月搜索量")) or 0)
        for b in COMP_BRANDS:
            if b in kw: c[COMP_DISPLAY.get(b.strip(),b.strip().title())]+=1+vol/100000.0  # 出现1次=+1,月搜量仅微调tiebreak
    return [b for b,_ in c.most_common(topn)]
def ads_tpl_local(cat,site,rows,soft=False):
    """#4 非英语站(MX/DE/FR/ES/IT/JP): 骨架+本站词库本地词, bid/预算留空运营按本地市场填。"""
    anchors=CAT_ANCHORS.get(cat,CAT_ANCHORS["dock"])
    def ok(kl):  # 广告核心词: 排 拼写/IP/硬别平台/纯console/竞品(商标nintendo保留,ad可投); 且必须品类/机型相关(防"sing meinen song"类高搜noise)
        if is_misspell(kl) or is_ip(kl) or is_hard_platform(kl) or is_pure_console(kl) or is_comp(kl): return False
        if soft_platform_hit(kl) and not soft: return False  # pc/手机词: listing没声明支持才剔(R4)
        if any(a in kl for a in anchors): return True
        return is_machine_compat(kl)  # 纯机型词(nintendo switch 2)放行;跨品类(switch 2 controller 对dock)剔除
    def pick(n,pred=None):
        c=[(float(ext(f.get("月搜索量")) or 0)+float(ext(f.get("已出单单量")) or 0)*5000, ext(f.get("关键词"))) for f in rows
           if f.get("矩阵")=="意图词" and ext(f.get("关键词")) and ok(ext(f.get("关键词")).lower()) and (pred is None or pred(ext(f.get("关键词")).lower()))]
        c.sort(reverse=True); seen=set(); o=[]
        for _,w in c:
            if w.lower() in seen: continue
            seen.add(w.lower()); o.append(w)
            if len(o)>=n: break
        return " | ".join(o)
    SELL=["hall","turbo","nfc","rgb","paddle","gatillo","4k","fan","ventilador","cooling","60hz","hdmi","ladestation"]
    GIFTL=["regalo","cadeau","geschenk","regalo gamer","weihnacht"]
    NF="待运营填(本地市场)"
    core=pick(4) or "(从本站词库选本地核心词)"; mid=pick(4) or core
    wb=lambda terms:(lambda k:any(re.search(r'\b'+re.escape(s)+r'\b',k) for s in terms))  # 词边界,防 fan 命中 fantasy
    sell=pick(4,wb(SELL)) or "(本站卖点词)"
    gift=pick(3,wb(GIFTL)) or "(本站礼品词:regalo/geschenk/cadeau)"
    R=lambda w:w+" · 非英语站:词从本站词库选,bid 本地市场待运营定"
    return [P(f"SP-Auto-捡词({site})","SP-Auto自动","自动(4匹配)","系统自动匹配",NF,NF,NF,"P1",R("起量挖本地搜索词")),
            P(f"SP-Exact-核心大词({site})","SP手动Exact","Exact",core,NF,NF,NF,"P1",R("本站核心词Exact卡位")),
            P(f"SP-Exact-中词扩量({site})","SP手动Exact","Exact",mid,NF,NF,NF,"P2",R("本站中词扩量")),
            P(f"SP-Broad-长尾({site})","SP手动Broad","Broad",core,NF,NF,NF,"P1",R("Broad发本地长尾")),
            P(f"SP-Exact-卖点簇({site})","SP手动Exact","Exact",sell,NF,NF,NF,"P2",R("本站卖点词")),
            P(f"SD-竞品定投({site})","SD商品定投","ASIN定投","本站竞品ASIN(按本地市场选)",NF,NF,NF,"P2",R("SD打本地竞品")),
            P(f"SBV-品牌簇({site})","SBV视频","Exact",core,NF,NF,NF,"P2",R("视频展示")),
            P(f"SP-Exact-礼品({site})","SP手动Exact","Exact",gift,NF,NF,NF,"Q4",R("Q4礼品季"))]
def _ads_tpl_base(cat,site,rows,soft=False):
    if site not in EN_SITES: return ads_tpl_local(cat,site,rows,soft)
    if cat=="controller": return [P("SP-Auto-手柄捡词","SP-Auto自动","自动(4匹配)","系统自动匹配","$0.45","$20","30%","P1","起量+挖搜索词;低bid捡漏"),P("SP-Exact-核心手柄大词","SP手动Exact","Exact","switch 2 controller | nintendo switch 2 controller | switch 2 pro controller","$1.0","$25","28%","P1","核心词Exact卡位"),P("SP-Exact-中词扩量","SP手动Exact","Exact","hall effect controller | switch controller wireless","$0.8","$20","30%","P2","中词扩量"),P("SP-Broad-手柄长尾","SP手动Broad","Broad","switch 2 controller with paddles | turbo controller switch","$0.5","$15","32%","P1","Broad发长尾(精准否锁大词)"),P("SP-Exact-卖点簇","SP手动Exact","Exact","hall effect joystick | back paddle controller | turbo | rgb controller","$0.7","$12","30%","P2","霍尔/背键/连发/RGB"),P("SD-竞品手柄定投","SD商品定投","ASIN定投","8bitdo/GameSir/NYXI 竞品ASIN","$0.6","$12","32%","P2","SD打竞品详情页"),P("SBV-手柄品牌簇","SBV视频","Exact","switch 2 controller","$1.0","$15","30%","P2","视频展示霍尔+握感"),P("SP-Exact-礼品词","SP手动Exact","Exact","gifts for gamers | switch gifts","$0.6","$10","32%","Q4","Q4礼品季")]
    if cat=="case": return [P("SP-Auto-卡盒捡词","SP-Auto自动","自动(4匹配)","系统自动匹配","$0.40","$15","30%","P1","起量+挖词"),P("SP-Exact-核心卡盒大词","SP手动Exact","Exact","switch 2 case | nintendo switch 2 case | switch 2 carrying case","$0.8","$20","28%","P1","核心词Exact卡位"),P("SP-Exact-中词扩量","SP手动Exact","Exact","switch 2 storage case | hard shell switch case | switch game holder","$0.6","$15","30%","P2","中词扩量"),P("SP-Broad-卡盒长尾","SP手动Broad","Broad","switch 2 travel case | slim case switch","$0.45","$12","32%","P1","Broad发长尾"),P("SP-Exact-卖点簇","SP手动Exact","Exact","hard shell switch 2 case | switch case 10 game","$0.55","$10","30%","P2","硬壳/卡槽/便携"),P("SD-竞品卡盒定投","SD商品定投","ASIN定投","tomtoc/Belkin 竞品ASIN","$0.5","$10","32%","P2","SD打竞品卡盒"),P("SBV-卡盒品牌簇","SBV视频","Exact","switch 2 case","$0.8","$12","30%","P2","展示卡槽+材质"),P("SP-Exact-礼品词","SP手动Exact","Exact","gifts for gamers | switch gifts","$0.5","$10","32%","Q4","Q4礼品季")]
    return [P("SP-Auto-dock捡词","SP-Auto自动","自动(4匹配)","系统自动匹配","$0.45","$20","28%","P1","起量+挖词"),P("SP-Exact-核心dock大词","SP手动Exact","Exact","switch 2 dock | nintendo switch 2 dock | switch 2 docking station","$1.2","$25","25%","P1","核心词Exact卡位"),P("SP-Exact-中词扩量","SP手动Exact","Exact","switch dock | switch 2 tv dock | switch 2 charging dock","$0.9","$20","28%","P2","中词扩量"),P("SP-Broad-dock长尾","SP手动Broad","Broad","switch 2 portable dock | switch oled dock","$0.5","$15","30%","P1","Broad发长尾"),P("SP-Exact-卖点簇","SP手动Exact","Exact","switch 2 dock with fan | switch 2 4k dock","$0.8","$12","28%","P2","散热/4K/充电"),P("SD-竞品dock定投","SD商品定投","ASIN定投","JSAUX/Genki 竞品ASIN","$0.6","$12","30%","P2","SD打竞品dock"),P("SBV-dock品牌簇","SBV视频","Exact","switch 2 dock","$1.0","$15","28%","P2","展示散热+4K"),P("SP-Exact-礼品词","SP手动Exact","Exact","gifts for gamers | switch gifts","$0.6","$10","32%","Q4","Q4礼品季")]
def ads_tpl(cat,site="US",rows=None,soft=False):
    rows=rows or []
    lst=_ads_tpl_base(cat,site,rows,soft)
    comp=local_comp_brands(rows)  # #翔宇: SD竞品定投用本站市场真实竞品(MX=PowerA/PDP/8bitdo)非美国硬编
    if comp:
        for p in lst:
            if "竞品" in p["计划名"]: p["包含关键词"]="竞品ASIN定投·本站市场: "+" / ".join(comp)+" (取自本站词库竞品;具体ASIN见表1「竞品前十ASIN」列;运营可补市场畅销竞品)"
    return lst
def fill_234(app,t1,t2,t3,t5,t6,L,cat,site):
    supp=supported_machines(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]); soft=supports_soft(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]+" "+L["st"])
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
        kl=kw.lower()
        if is_misspell(kl): status="拼写变体(广告可投·勿写listing)"
        elif is_trademark(kl): status=("⚠️商标在标题/五点·撤(仅描述/ST用for-para措辞)" if (inT or inB) else ("ST合规(for形式)+UGC" if inS else "仅for/compatible措辞+UGC"))
        elif is_comp(kl) or is_ip(kl) or mx in ("品牌词-竞品","IP词"): status="UGC引导(勿直写)"
        elif fr or inS: status="已埋" if fr else "已埋(ST)"
        elif mx in ("意图词","品牌词-平台"): status="待埋(补描述)" if qualify_embed(kw,cat,supp,soft) else "不埋"
        else: status="UGC待引导"
        t2r.append({"关键词":kw,"站点":site,"矩阵":mx,"埋词渠道":ch,"标题已埋":inT,"五点已埋":inB,"描述已埋":inD,"后台ST已埋":inS,"前台已覆盖":fr,"埋词状态":status})
    n2=batch(app,t2,t2r)
    n5=0
    if not lall(app,t5):
        n5=batch(app,t5,[{"阶段":"P1 (0-30d)","阶段目标":"低SPR小词冲首页+核心品类词建联","关键KPI":"核心词进首页;Auto挖词反哺","农村是否生效":"观察中","下阶段触发条件":"核心词稳定P1"},{"阶段":"P2 (30-60d)","阶段目标":"大词排名爬升+中词扩量+补埋","关键KPI":"大词进前2页;簇收录率>50%","农村是否生效":"观察中","下阶段触发条件":"大词进前2页+ACoS可控"},{"阶段":"P3 (60d+)","阶段目标":"核心词进前10转防守+SD打竞品","关键KPI":"核心词稳定前10","农村是否生效":"观察中","下阶段触发条件":"前10稳定2周"}])
    ensure_site(app,t3); clear(app,t3,lambda f:f.get("站点")==site)  # per-site: 多站点app每站独立广告框架(本地语言词)
    t3rows=ads_tpl(cat,site,rows,soft)
    for p in t3rows: p["站点"]=site
    n3=batch(app,t3,t3rows)
    ensure_site(app,t6); clear(app,t6,lambda f:f.get("站点")==site); out=[]; seen=set()  # per-site 否定词
    def add(w,way,c,note):
        wl=w.strip().lower()
        if wl and wl not in seen: seen.add(wl); out.append({"否定词":w.strip(),"站点":site,"否定方式":way,"类别":c,"状态":"待添加","应用范围":"全广告活动","备注":note})
    for w in ["switch","nintendo switch","switch 2","nintendo switch 2","nintendo","switch oled","nintendo switch oled","switch lite","steam deck","steamdeck"]: add(w,"精准否定","大词/品牌/泛词","裸平台大词:只否精确,留Broad发长尾")
    for c in COLORS: add(c,"词组否定","颜色词","本品单色,其余颜色整片否(运营留自己色)")
    for w in PRICE: add(w,"词组否定","其他(配件/平台)","价格/二手意图")
    for w in CROSS.get(cat,[]): add(w,"词组否定","其他(配件/平台)","别品类配件,整片屏蔽")
    for w in ["xbox","ps5","ps4","ps3","playstation","dualsense","steam controller"]: add(w,"词组否定","其他(配件/平台)","别平台,整片屏蔽")
    if not soft:  # R4: listing没声明支持PC/手机才否; 声明支持则pc/android是有效兼容流量不否
        for w in ["pc","android"]: add(w,"词组否定","其他(配件/平台)","别平台(本品未声明PC/手机支持);若支持则listing补一句并移除此否定")
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
            c=classify_report(os.path.basename(x),asin,lin); files.setdefault(c,[]).append(x)
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

# ───────────────── L3 每周复审 ─────────────────
def compute_audit(L,rows,cat):
    """rows = 表1 fields dict 列表(已按站点过滤)。返回审计指标 dict(给周快照+delta用)。"""
    supp=supported_machines(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]); soft=supports_soft(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]+" "+L["st"])
    tt=set(toks(L["title"])); bt=set()
    for b in L["bullets"]: bt|=set(toks(b))
    dt=set(toks(L["desc"])); st=set(toks(L["st"])); front=tt|bt|dt
    def cov(kw,s): k=toks(kw); return bool(k) and all(w in s for w in k)
    R=[]
    for f in rows:
        kw=ext(f.get("关键词"))
        R.append({"kw":kw,"mx":f.get("矩阵"),"vol":float(ext(f.get("月搜索量")) or 0),"ord":float(ext(f.get("已出单单量")) or 0),
                  "rank":float(ext(f.get("我方自然排名")) or 0),"front":cov(kw,front),"instr":cov(kw,st),"qual":qualify_embed(kw,cat,supp,soft)})
    total=len(R); embedded=sum(1 for r in R if r["front"] or r["instr"])
    rk=[r for r in R if r["rank"]>0]; p1=[r for r in rk if r["rank"]<=16]; p23=[r for r in rk if 16<r["rank"]<=48]; deep=[r for r in rk if r["rank"]>48]
    sens=lambda r: r["mx"] in ("IP词","品牌词-竞品") or is_ip(r["kw"]) or is_comp(r["kw"]) or is_trademark(r["kw"])
    ugc=[r for r in R if sens(r)]; embeddable=[r for r in R if r["qual"] and not sens(r)]
    miss=agg_roots(sorted([r for r in embeddable if not(r["front"] or r["instr"])],key=lambda r:-(r["vol"]+r["ord"]*5000)))
    be=len(L["bullets"])==0; de=not L["desc"].strip(); se=not L["st"].strip(); buy="BUYABLE" in (L["status"] or [])
    notext=(not L["title"].strip()) and be and de and se
    status="空listing" if notext else ("半成品" if (be or de or se or not buy) else "正常")
    return {"total":total,"recorded":len(rk),"p1":len(p1),"p23":len(p23),"deep":len(deep),"embedded":embedded,
            "cover_pct":round(100.0*embedded/max(total,1)),"fit":len(embeddable)+len(ugc),
            "miss":[{"kw":r["kw"],"vol":r["vol"],"ord":r["ord"]} for r in miss[:5]],"status":status}

def refresh_t2(app,t1,t2,L,cat,site):
    """只刷表2(Listing埋词审计),保持与最新 listing 文案同步(摘自 fill_234 表2 段)。"""
    supp=supported_machines(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]); soft=supports_soft(L["title"]+" "+" ".join(L["bullets"])+" "+L["desc"]+" "+L["st"])
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
        kl=kw.lower()
        if is_misspell(kl): status="拼写变体(广告可投·勿写listing)"
        elif is_trademark(kl): status=("⚠️商标在标题/五点·撤(仅描述/ST用for-para措辞)" if (inT or inB) else ("ST合规(for形式)+UGC" if inS else "仅for/compatible措辞+UGC"))
        elif is_comp(kl) or is_ip(kl) or mx in ("品牌词-竞品","IP词"): status="UGC引导(勿直写)"
        elif fr or inS: status="已埋" if fr else "已埋(ST)"
        elif mx in ("意图词","品牌词-平台"): status="待埋(补描述)" if qualify_embed(kw,cat,supp,soft) else "不埋"
        else: status="UGC待引导"
        t2r.append({"关键词":kw,"站点":site,"矩阵":mx,"埋词渠道":ch,"标题已埋":inT,"五点已埋":inB,"描述已埋":inD,"后台ST已埋":inS,"前台已覆盖":fr,"埋词状态":status})
    return batch(app,t2,t2r)

def im_card(oid,title,md,color="blue"):
    card={"config":{"wide_screen_mode":True},"header":{"template":color,"title":{"tag":"plain_text","content":title}},
          "elements":[{"tag":"div","text":{"tag":"lark_md","content":md}}]}
    api("POST","/im/v1/messages?receive_id_type=open_id",{"receive_id":oid,"msg_type":"interactive","content":json.dumps(card,ensure_ascii=False)})

def _arrow(d):
    if d>0: return f"<font color='green'>↑{d}</font>"
    if d<0: return f"<font color='red'>↓{abs(d)}</font>"
    return "持平"

def store_ad_map(sid):
    """{asin:[campaign dicts含state]} 该店各ASIN的SP活动。"""
    try:
        ads=lx("/pb/openapi/newad/spProductAds",{"sid":sid,"offset":0,"length":500}).get("data") or []
        camps={str(c.get("campaign_id")):c for c in (lx("/pb/openapi/newad/spCampaigns",{"sid":sid,"offset":0,"length":500}).get("data") or [])}
        m={}
        for a in ads:
            if a.get("asin"): m.setdefault(a["asin"],set()).add(str(a.get("campaign_id")))
        return {asin:[camps.get(c,{"state":"?","name":"?"}) for c in cids] for asin,cids in m.items()}
    except Exception: return {}
def store_ad_perf(sid,days=7):
    """{asin:{impr,clicks,cost,orders,sales}} 近days天SP广告效果(spProductAdReports单日report_date循环聚合)。"""
    agg={}; today=datetime.date.today()
    try:
        for i in range(1,days+1):
            d=(today-datetime.timedelta(days=i)).isoformat()
            rows=lx("/pb/openapi/newad/spProductAdReports",{"sid":sid,"report_date":d,"offset":0,"length":300}).get("data") or []
            if isinstance(rows,dict): rows=rows.get("list") or []
            for r in rows:
                a=r.get("asin")
                if not a: continue
                x=agg.setdefault(a,{"impr":0,"clicks":0,"cost":0.0,"orders":0,"sales":0.0})
                x["impr"]+=r.get("impressions") or 0; x["clicks"]+=r.get("clicks") or 0
                x["cost"]+=float(r.get("cost") or 0); x["orders"]+=r.get("orders") or 0; x["sales"]+=float(r.get("sales") or 0)
    except Exception: pass
    return agg
def store_listing_meta(sid):
    """{asin:{bsr,fba,thirty}} BSR(seller_rank)+FBA可售(afn_fulfillable)+30天销量(mws/listing分页cap2000)。"""
    out={}; off=0
    try:
        while off<2000:
            d=lx("/erp/sc/data/mws/listing",{"sid":sid,"length":200,"offset":off}).get("data") or []
            for it in d:
                a=it.get("asin")
                if a and a not in out: out[a]={"bsr":it.get("seller_rank"),"fba":it.get("afn_fulfillable_quantity"),"thirty":it.get("thirty_volume")}
            if len(d)<200: break
            off+=len(d)
    except Exception: pass
    return out
def do_review(frankie_only=False,dry=False):
    day=time.strftime("%Y-%m-%d")
    _adc={}; _metac={}; _perfc={}
    def admap(sid):
        if sid not in _adc: _adc[sid]=store_ad_map(sid)
        return _adc[sid]
    def metam(sid):
        if sid not in _metac: _metac[sid]=store_listing_meta(sid)
        return _metac[sid]
    def perfm(sid):
        if sid not in _perfc: _perfc[sid]=store_ad_perf(sid)
        return _perfc[sid]
    reg=[r for r in lall(REG_APP,REG_TB) if r["fields"].get("状态") in ("在跑","筹备")]
    snaps=lall(REG_APP,SNAP_TB)
    prev={}
    for s in snaps:
        f=s["fields"]; k=(ext(f.get("ASIN")),f.get("站点")); ts=f.get("快照时间") or 0
        if k not in prev or ts>prev[k][0]: prev[k]=(ts,f)
    now=int(time.time()*1000); per_op={}; new_snap=[]; errors=[]
    for r in reg:
        f=r["fields"]
        product=ext(f.get("产品")); site=f.get("站点"); asin=ext(f.get("ASIN")); region=f.get("区域")
        op=ext(f.get("负责运营")); cat=ext(f.get("品类")) or "controller"
        sid=int(ext(f.get("店铺sid")) or 0); sku=ext(f.get("seller_sku"))
        app2=ext(f.get("作战台App_token")); t1=ext(f.get("词库表id"))
        haverank=bool(ext(f.get("rank子表id"))) and f.get("状态")=="在跑"
        try:
            if not sku and sid: sku=lookup_sku(sid,asin)
            if not sku: errors.append(f"{product}-{site}:无sku"); continue
            lr=lx("/listing/publish/openapi/amazon/product/search",{"store_id":sid,"skus":[sku]})
            L=load_listing(lr)
            rows=[x["fields"] for x in lall(app2,t1) if x["fields"].get("站点")==site]
            m=compute_audit(L,rows,cat)
            meta=metam(sid).get(asin,{}); camps=admap(sid).get(asin,[]); perf=perfm(sid).get(asin,{})
            bsr=meta.get("bsr"); fba=meta.get("fba"); thirty=meta.get("thirty")
            enabled=[c for c in camps if c.get("state")=="enabled"]  # state持久(不随时间/dayparting变)
            serving=[c for c in enabled if "ENABLED" in (c.get("serving_status") or "")]  # 真在投(CAMPAIGN_STATUS_ENABLED)
            oob=[c for c in enabled if "OUT_OF_BUDGET" in (c.get("serving_status") or "")]  # 开了但预算耗尽
            instock=isinstance(fba,(int,float)) and fba>=10; ranked=isinstance(bsr,(int,float)) and bsr>0
            derelict=instock and ranked and len(enabled)==0  # 有货+有BSR+0个enabled活动=没开广告(全暂停/无)=失职
            budget_alert=instock and ranked and len(enabled)>0 and len(serving)==0  # 开了但0在投(预算耗尽等)≠失职,催提预算
            impr=perf.get("impr",0); clk=perf.get("clicks",0); cost=round(perf.get("cost",0.0),2); ords=perf.get("orders",0); sal=perf.get("sales",0.0)
            ctr=round(100.0*clk/impr,2) if impr else 0; cvr=round(100.0*ords/clk,1) if clk else 0; acos=round(100.0*cost/sal,1) if sal else 0
            perf_v=ad_verdict(acos,sal)  # 健康/ACoS偏高/无成交 — 供"是否值得提预算"
            if budget_alert:
                budget_advice=("✅值得提预算(ACoS"+str(acos)+"%健康,预算限了放量)" if perf_v=="健康"
                    else ("⚠️别盲目提预算·先优化(ACoS"+str(acos)+"%偏高,CTR"+str(ctr)+"%/CVR"+str(cvr)+"%) → 降bid/加否词/改listing" if perf_v=="ACoS偏高"
                    else "数据不足(近7天无成交),先观察/查广告相关性"))
            else: budget_advice=""
            if not dry:
                try:
                    tm={x["name"]:x["table_id"] for x in api("GET",f"/bitable/v1/apps/{app2}/tables?page_size=100")["data"]["items"]}
                    refresh_t2(app2,t1,tm["表2·Listing埋词审计"],L,cat,site)
                except Exception: pass
            pv=prev.get((asin,site),(0,{}))[1]
            def _pn(v):
                try: return float(v or 0)
                except Exception: return 0.0
            if pv:
                d_cov=m["cover_pct"]-_pn(pv.get("埋词覆盖率")); d_rec=m["recorded"]-_pn(pv.get("已收录")); d_p1=m["p1"]-_pn(pv.get("首页"))
            else: d_cov=d_rec=d_p1=0
            res={"product":product,"site":site,"region":region,"op":op,"asin":asin,"haverank":haverank,
                 "m":m,"first":not pv,"d_cov":d_cov,"d_rec":d_rec,"d_p1":d_p1,
                 "bsr":bsr,"fba":fba,"thirty":thirty,"ncamp":len(camps),"nen":len(enabled),"nrun":len(serving),"noob":len(oob),
                 "derelict":derelict,"budget_alert":budget_alert,"cost":cost,"acos":acos,"ctr":ctr,"cvr":cvr,"perf_v":perf_v,"budget_advice":budget_advice}
            per_op.setdefault(op,[]).append(res)
            new_snap.append({"快照键":f"{asin}-{site}-{now}","产品":product,"站点":site,"ASIN":asin,"区域":region,"负责运营":op,
                "候选数":m["total"],"已收录":m["recorded"],"首页":m["p1"],"2-3页":m["p23"],"靠后":m["deep"],"已埋":m["embedded"],
                "埋词覆盖率":m["cover_pct"],"合适词":m["fit"],"埋词覆盖Δ":d_cov,"收录Δ":d_rec,"首页Δ":d_p1,
                "listing状态":m["status"],"有rank追踪":"是" if haverank else "否","快照时间":now,
                "BSR":bsr if ranked else None,"FBA可售":fba if isinstance(fba,(int,float)) else None,
                "广告活动":len(camps),"广告在跑":len(serving),"广告在投":len(serving),"预算耗尽":len(oob),"失职":"是" if derelict else "否",
                "7天花费":cost,"ACoS%":acos,"CTR%":ctr,"CVR%":cvr})
        except Exception as e:
            errors.append(f"{product}-{site}:{str(e)[:80]}")
    if new_snap and not dry: batch(REG_APP,SNAP_TB,new_snap)
    # 每运营卡
    if not frankie_only and not dry:
        for op,items in per_op.items():
            oid=OP_OID.get(op)
            if not oid: continue
            lines=[]
            for it in sorted(items,key=lambda x:(x["m"]["status"]=="正常",-x["m"]["cover_pct"])):
                m=it["m"]; tag="" if it["first"] else f" (覆盖{_arrow(it['d_cov'])} 收录{_arrow(it['d_rec'])} 首页{_arrow(it['d_p1'])})"
                lines.append(f"**{it['product']} {it['site']}** · 埋词覆盖 {m['cover_pct']}% · 已收录 {m['recorded']}(首页{m['p1']}) · 合适词 {m['fit']}{tag}")
                if m["status"]!="正常": lines.append(f"  🔴 listing **{m['status']}** → 先补全文案再谈埋词")
                elif not it["haverank"]: lines.append("  ⚪ 收录追踪未铺开(以埋词覆盖率为准)")
                if m["miss"]: lines.append("  📌 漏埋高价值: "+" / ".join(x["kw"] for x in m["miss"][:4]))
                if not it["first"] and it["d_cov"]<0: lines.append("  ⚠️ 埋词覆盖**退步**,核对是否改 listing 改丢了词")
                if it.get("derelict"): lines.append(f"  🔴 **失职**: 有货(FBA{it['fba']})+BSR#{it['bsr']} 但 **0个广告开启**({it['ncamp']}活动全暂停/无) → 立即开广告")
                elif it.get("budget_alert"): lines.append(f"  🟠 **预算耗尽**: {it['nen']}活动开着但0在投(预算超{it['noob']}) → {it['budget_advice']}")
                elif it.get("nrun",0)>0:
                    pv=it.get("perf_v"); tail=("· 表现健康" if pv=="健康" else (f"· **ACoS偏高该优化**(降bid/加否词/改listing)" if pv=="ACoS偏高" else "· 近7天无成交,查相关性"))
                    lines.append(f"  {'📣' if pv=='健康' else '⚠️'} 广告{it['nrun']}在投 · 7天花${it['cost']} ACoS{it['acos']}% CTR{it['ctr']}% CVR{it['cvr']}% {tail} · FBA{it['fba']} BSR#{it['bsr']}")
            md=f"**{day} 万词周自检** · 你负责 {len(items)} 个作战台\n\n"+"\n".join(lines)+"\n\n> 详情开作战台表2;改 listing/开广告 是你的活,系统只审不改。"
            im_card(oid,f"🟡 [AMZ·P2] 万词周自检 · {op}",md,"orange")
    # Frankie 总digest
    foid=OP_OID.get("潘志聪")
    if foid:
        allr=[x for v in per_op.values() for x in v]
        improved=[x for x in allr if not x["first"] and (x["d_cov"]>0 or x["d_rec"]>0)]
        stuck=[x for x in allr if x["m"]["status"]!="正常" or (not x["first"] and x["d_cov"]<0)]
        L1=[f"✅ {x['product']} {x['site']}: 覆盖{_arrow(x['d_cov'])} 收录{_arrow(x['d_rec'])}" for x in improved] or ["（本周无明显改善）"]
        L2=[f"🔴 {x['product']} {x['site']}: "+("listing "+x['m']['status'] if x['m']['status']!='正常' else f"埋词覆盖退步{_arrow(x['d_cov'])}")+f" — 催 {x['op']}" for x in stuck] or ["（无卡住）"]
        derel=[x for x in allr if x.get("derelict")]; budg=[x for x in allr if x.get("budget_alert")]
        L4=[f"🔴 {x['product']} {x['site']}: FBA{x['fba']}有货+BSR#{x['bsr']} 但0个广告开启 — 催 {x['op']}" for x in derel] or ["（无）"]
        L5=[f"🟠 {x['product']} {x['site']}(预算超{x['noob']}): {x['budget_advice']} — {x['op']}" for x in budg] or ["（无）"]
        base="(首轮=建立基线,delta 下周起有效)" if all(x["first"] for x in allr) else ""
        md=(f"**{day} 万词周自检总览** · {len(allr)}个作战台 {base}\n\n"
            f"**🔴 运营失职·有货有排名却0广告开启 {len(derel)}**\n"+"\n".join(L4)
            +f"\n\n**🟠 预算耗尽·开了但0在投 {len(budg)}**\n"+"\n".join(L5)
            +f"\n\n**📈 埋词改善 {len(improved)}**\n"+"\n".join(L1)+f"\n\n**🚨 listing卡住 {len(stuck)}**\n"+"\n".join(L2)
            +(f"\n\n**⚠️ 异常 {len(errors)}**: "+" / ".join(errors[:8]) if errors else "")
            +f"\n\n> 失职=可售≥10+有BSR+0个enabled活动(全暂停);预算耗尽=开了但全OUT_OF_BUDGET。**预算建议按ACoS表现**:ACoS≤{int(TARGET_ACOS)}%健康才提预算放量,ACoS高/无成交→先优化勿烧钱。判定用持久state不受dayparting/查询时点影响;均豁免断货/非BUYABLE。")
        im_card(foid,f"🟡 [AMZ·P2] 万词周自检总览 · {day}",md,"blue")
    return {"ok":True,"reviewed":len(per_op),"snap":len(new_snap),"errors":errors}

app=FastAPI()
@app.get("/")
def root(): return {"service":"wanci-onboard","ok":True}
@app.post("/review")
async def review(req:Request):
    if AUTH_TOKEN and req.headers.get("authorization","")!="Bearer "+AUTH_TOKEN: return {"ok":False,"err":"unauthorized"}
    try: body=await req.json()
    except Exception: body={}
    threading.Thread(target=do_review,kwargs={"frankie_only":bool(body.get("frankie_only")),"dry":bool(body.get("dry_run"))},daemon=True).start()
    return {"ok":True,"msg":"review started","frankie_only":bool(body.get("frankie_only")),"dry":bool(body.get("dry_run"))}
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
