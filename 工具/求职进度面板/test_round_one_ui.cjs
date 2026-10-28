const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, 'dashboard_template.html'), 'utf8');
function fn(name) {
  const start = html.indexOf('  function ' + name + '(');
  assert.ok(start >= 0, 'missing behavior: ' + name);
  let depth=0,quote='',escaped=false;
  for(let i=html.indexOf('{',start);i<html.length;i++) {
    const c=html[i]; if(quote){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c===quote)quote='';continue;}
    if(c==='"'||c==="'"){quote=c;continue;} if(c==='{')depth++;if(c==='}'&&--depth===0)return html.slice(start,i+1);
  }
  throw new Error(name);
}
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.listeners={};this.style={};this.hidden=false;this.open=false;this._text='';}
  set textContent(v){this._text=String(v);this.children=[];}
  get textContent(){return this._text+this.children.map(x=>typeof x==='string'?x:x.textContent).join('');}
  appendChild(x){this.children.push(x);return x;}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this.listeners[k]=v;}
  focus(){document.activeElement=this;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  querySelectorAll(){return this.focusables||[];}
}
const nodes=new Map();
const document={activeElement:null,createElement:t=>new Element(t),getElementById(id){if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);}};
const state={groups:new Set(['waiting']),q:'filtered',range:'30',hcOnly:true};
const apps=[{id:'app-1',company:'Example',req:'REQ-ONE',title:'Scientist',stage_label:'测评中'},
{id:'hidden',company:'Hidden',req:'SECRET',title:'Not visible'}];
const ctx=vm.createContext({document,APPS:apps,TP:{todo:[{uid:'candidate:1',company:'Example Labs',req:'REQ-TWO',title:'Engineer',blockers:'Confirm qualification'}]},D:{internships:{items:[{id:'intern:1',company:'Example Research',req:'REQ-THREE',title:'Intern'}]}},state,companyFilter:{matches:x=>x.company!=='Hidden'},drawerReturnFocus:null});
vm.runInContext(['el','companyMatches','globalSearchEntries','openDrawer','closeDrawer','drawerKeydown','jumpTo'].map(fn).join('\n'),ctx);
const results=vm.runInContext('globalSearchEntries("req")',ctx);
assert.equal(results.length,3,'search covers all three source types');
assert.deepEqual(Array.from(results,x=>x.kind).sort(),['application','candidate','internship']);
assert.equal(vm.runInContext('globalSearchEntries("SECRET").length',ctx),0,'hidden company stays hidden');
assert.equal(vm.runInContext('globalSearchEntries("req-two")[0].item.uid',ctx),'candidate:1');
assert.equal(vm.runInContext('globalSearchEntries("zzz").length',ctx),0);
let opened=null;ctx.openApplicationDetail=a=>{opened=a;};
const before={q:state.q,range:state.range,hcOnly:state.hcOnly,groups:[...state.groups]};
vm.runInContext('jumpTo("app-1")',ctx);assert.equal(opened.id,'app-1');
assert.deepEqual({q:state.q,range:state.range,hcOnly:state.hcOnly,groups:[...state.groups]},before,'detail navigation preserves filters');
vm.runInContext('jumpTo("hidden")',ctx);assert.equal(opened.id,'app-1','hidden company cannot be opened');
const source=new Element('button');document.activeElement=source;
vm.runInContext('openDrawer("Example", [el("p", {text:"Safe <script> content"})])',ctx);
const drawer=nodes.get('detail-drawer');assert.equal(drawer.open,true);assert.equal(nodes.get('drawer-title').textContent,'Example');
assert.equal(nodes.get('drawer-body').children[0].tag,'p');
let prevented=false;ctx.event={key:'Escape',preventDefault(){prevented=true;}};
vm.runInContext('drawerKeydown(event)',ctx);assert.equal(prevented,true);assert.equal(drawer.open,false);assert.equal(document.activeElement,source,'Escape restores original focus');
vm.runInContext('openDrawer("Again", [])',ctx);
const first=new Element('button'),last=new Element('a');drawer.focusables=[first,last];document.activeElement=last;
ctx.event={key:'Tab',shiftKey:false,preventDefault(){}};vm.runInContext('drawerKeydown(event)',ctx);assert.equal(document.activeElement,first,'Tab wraps within drawer');
document.activeElement=first;ctx.event={key:'Tab',shiftKey:true,preventDefault(){}};vm.runInContext('drawerKeydown(event)',ctx);assert.equal(document.activeElement,last,'Shift+Tab wraps within drawer');
console.log('PASS round-one UI: global lookup, exact record selection, preserved filters, safe text, Escape focus return and keyboard trap');
