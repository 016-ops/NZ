'use strict';
let session = null, status = null, currentView = 'me';
const $ = id => document.getElementById(id);
const labels = {me:'我的账号',users:'用户管理',invitations:'邀请管理',resources:'资源中心',audit:'审计记录',outbox:'同步任务',rooms:'房间控制',catalog:'版本目录',dashboard:'运行状态'};
function node(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
function feedback(value) { $('feedback').hidden = false; $('message').textContent = typeof value === 'string' ? value : JSON.stringify(value,null,2); }
async function api(path, data) {
  const headers = {}; if (session) headers.Authorization = `Bearer ${session.token}`;
  const options = {headers, cache:'no-store'};
  if (data !== undefined) { options.method='POST'; headers['Content-Type']='application/json'; headers['X-NZ-Request']='1'; options.body=JSON.stringify(data); }
  const response = await fetch(path,options); const result = await response.json();
  if (!response.ok) { if (response.status===401) { session=null; loggedIn(); } throw new Error(`${result.error}: ${result.message}`); }
  return result;
}
function metadata(reason='本地开发操作') { return {request_id:crypto.randomUUID(), idempotency_key:crypto.randomUUID(), reason}; }
function button(title, onClick) { const b=node('button',title); b.type='button'; b.addEventListener('click', async () => {b.disabled=true; try {await onClick();} catch(e){feedback(e.message);} finally{b.disabled=false;}}); return b; }
function field(form,name,title,type='text',options=null) {
  const label=node('label',title); const input=options ? node('select') : node('input'); input.name=name;
  if (options) for(const [value,text] of options) {const option=node('option',text);option.value=value;input.append(option);}
  else input.type=type;
  input.required=true; if(type==='password') input.autocomplete='off'; label.append(input);form.append(label);return input;
}
function form(parent,title,fields,onSubmit) {
  const f=node('form',undefined,'inline-form');parent.append(node('h3',title),f);
  for(const spec of fields) field(f,...spec);
  const submit=node('button','提交');f.append(submit);
  let saved=null, payload='';
  f.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;try{
    const values=Object.fromEntries(new FormData(f)); const serialized=JSON.stringify(values);
    if(serialized!==payload){saved=metadata(values.reason||title);payload=serialized;}
    const result=await onSubmit(values,saved);feedback(result); saved=null;payload='';
  }catch(e){feedback(e.message);}finally{submit.disabled=false;}});return f;
}
async function command(action,data,meta=metadata()) { return api('/api/command',{...data,...meta,action}); }
function loggedIn(){
  $('auth').hidden=!!session;$('workspace').hidden=!session;$('logout').hidden=!session;
  const nav=$('navigation');nav.replaceChildren();if(!session)return;
  const views=session.kind==='admin'?['dashboard','users','invitations','resources','rooms','catalog','audit','outbox']:['me','rooms','catalog'];
  for(const view of views)nav.append(button(labels[view],()=>show(view)));
}
function jsonCard(parent,title,value){const card=node('div',undefined,'card');card.append(node('h3',title),node('pre',JSON.stringify(value,null,2)));parent.append(card);return card;}
async function show(view){
  currentView=view;$('view-title').textContent=labels[view];const actions=$('actions'),rows=$('rows');actions.replaceChildren();rows.replaceChildren();
  if(view==='resources'){await resourceForm(actions,rows);return;}
  const result=await api(`/api/views/${view}`);
  if(view==='me'){
    jsonCard(rows,'独立本地账号',result);
    if(!result.character) form(actions,'初始化本地角色（原客户端首登尚未接入）', [['nickname','昵称']],(data,meta)=>command('character.create',data,meta));
    else if(status.test_profile){
      form(actions,'购买测试装备',[['offer_key','商品','text',[['test-rifle-gp','测试步枪 / 500 测试 GP']]]],(data,meta)=>command('shop.purchase',{...data,catalog_version:'test-only-v1'},meta));
      for(const item of result.inventory){const card=jsonCard(rows,item.item_key,item);
        if(item.item_key==='test-rifle')card.append(button('装备到下局快照',async()=>{feedback(await command('inventory.equip',{instance_id:item.id,slot:'primary',catalog_version:'test-only-v1'}));await show('me');}));
        if(item.item_key==='test-token')card.append(button('消耗一个测试道具',async()=>{feedback(await command('inventory.consume',{instance_id:item.id,quantity:1,catalog_version:'test-only-v1'}));await show('me');}));
      }
    }
  }else if(view==='users'){
    form(actions,'查询用户',[['search','账号搜索']],async values=>{const found=await api(`/api/views/users?search=${encodeURIComponent(values.search)}`);rows.replaceChildren();renderUsers(rows,found);return {count:found.length};});renderUsers(rows,result);
  }else if(view==='invitations'){
    form(actions,'生成邀请（令牌只展示一次）',[['max_uses','最多使用次数','number'],['hours','有效小时','number'],['reason','原因']],(values,meta)=>command('invite.create',{max_uses:Number(values.max_uses),expires_at:Math.floor(Date.now()/1000)+Number(values.hours)*3600},meta));
    for(const item of result){const card=jsonCard(rows,item.id,item);if(!item.revoked)card.append(button('撤销邀请',async()=>{feedback(await command('invite.revoke',{invitation_id:item.id}));await show(view);}));}
  }else if(view==='rooms'){
    if(session.kind==='player'){
      if(status.test_profile)form(actions,'建立合成测试房间',[['map_key','地图','text',[['test-arena','测试竞技场 / 4 席，仅控制状态测试']]]],(values,meta)=>command('room.create',values,meta));
      else actions.append(node('p','地图、人数规则和原生实例未验证，建房入口尚未开放。'));
    }
    for(const room of result){const card=jsonCard(rows,room.id,room);
      if(session.kind==='player'){
        card.append(button('加入',async()=>{feedback(await command('room.join',{room_id:room.id}));await show(view);}));
        card.append(button('准备',()=>command('room.ready',{room_id:room.id,ready:true}).then(feedback)));
        card.append(button('退出',async()=>{feedback(await command('room.leave',{room_id:room.id}));await show(view);}));
        card.append(button('申请开始',async()=>{feedback(await command('room.start',{room_id:room.id,expected_revision:room.revision}));await show(view);}));
        if(['ALLOCATING','LOADING'].includes(room.state))card.append(button('取消分配',async()=>{feedback(await command('room.cancel',{room_id:room.id}));await show(view);}));
      }
    }
  }else if(Array.isArray(result)){for(const row of result)jsonCard(rows,row.id||row.operation_id||row.currency||'记录',row);}
  else jsonCard(rows,labels[view],result);
  actions.append(button('刷新',()=>show(currentView)));
}
function renderUsers(parent,users){
  for(const user of users){const card=jsonCard(parent,user.login,user);
    card.append(button('详情',async()=>feedback(await api(`/api/views/user?account_id=${encodeURIComponent(user.id)}`))));
    for(const [label,action] of [['批准','user.approve'],['停用','user.disable'],['激活','user.activate'],['踢下线','session.revoke']])card.append(button(label,async()=>{feedback(await command(action,{target_account_id:user.id}));await show('users');}));
    form(card,'全服封禁',[['reason','原因'],['hours','有效小时（0 为永久）','number']],(data,meta)=>command('sanction.create',{target_account_id:user.id,expires_at:Number(data.hours)===0?null:Math.floor(Date.now()/1000)+Number(data.hours)*3600},meta));
    form(card,'撤销指定封禁',[['sanction_id','封禁 ID'],['reason','原因']],(data,meta)=>command('sanction.revoke',{sanction_id:data.sanction_id},meta));
    form(card,'重置独立玩家凭据',[['new_password','新密码','password'],['reason','原因']],(data,meta)=>command('user.reset',{target_account_id:user.id,new_password:data.new_password},meta));
  }
}
async function resourceForm(parent,rows){
  const catalog=await api('/api/views/catalog');
  if(!Object.keys(catalog.currencies).length){parent.append(node('p','当前客户端货币映射与物品目录尚未验证，资源写入默认拒绝。'));return;}
  const users=await api('/api/views/users');if(!users.length){parent.append(node('p','先邀请、批准用户并初始化本地角色。'));return;}
  parent.append(node('p','测试币种范围仅适用于 TEST_PROFILE；提交前重新核验余额与库存。'));
  form(parent,'发放预览',[['target_account_id','目标','text',users.map(u=>[u.id,u.login])],['type','资源类型','text',[['currency','测试币种'],['item','测试物品']]],['resource','币种代码或测试物品 key'],['amount','增量 / 数量','number'],['reason','原因']],async(values,meta)=>{
    const change=values.type==='currency'?{type:'currency',code:values.resource,delta:Number(values.amount)}:{type:'item',item_key:values.resource,quantity:Number(values.amount)};
    const data={target_account_id:values.target_account_id,catalog_version:catalog.id,changes:[change],...meta};
    const result=await api('/api/asset-preview',data);rows.replaceChildren();jsonCard(rows,'提交前预览',result);
    const confirm=button('确认本次增量操作',async()=>{const committed=await command('asset.grant',data,meta);feedback(committed);confirm.remove();});rows.append(confirm);return {preview:'已生成，请在预览下确认'};
  });
  form(parent,'查询流水',[['account_id','目标','text',users.map(u=>[u.id,u.login])]],async values=>{const entries=await api(`/api/views/operations?account_id=${encodeURIComponent(values.account_id)}`);rows.replaceChildren();for(const entry of entries)jsonCard(rows,entry.id,entry);return {count:entries.length};});
}
$('login').addEventListener('submit',async event=>{event.preventDefault();try{const data=Object.fromEntries(new FormData(event.target));session=await api('/api/login',data);event.target.reset();loggedIn();await show(session.kind==='admin'?'dashboard':'me');}catch(e){feedback(e.message);}});
$('register').addEventListener('submit',async event=>{event.preventDefault();try{feedback(await api('/api/register',{...Object.fromEntries(new FormData(event.target)),...metadata('受邀本地注册')}));event.target.reset();}catch(e){feedback(e.message);}});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{});}finally{session=null;loggedIn();$('rows').replaceChildren();$('feedback').hidden=true;}});
api('/api/status').then(value=>{status=value;$('profile').textContent=`${value.profile} · ${value.evidence}`;if(value.test_profile)$('notice').textContent='TEST_PROFILE：合成账号、房间及资产测试环境。原客户端登录、地图战斗与同步尚未接入。';}).catch(e=>feedback(e.message));
