/** 签到和五类抽奖配置；先预览再发布，失败重试保留同一请求，初次签到保持停用。 */
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Details } from '@/components/business'
import { Api, RetryEnvelope, type Catalog, type Data, type Json } from '@/lib/api'

type Reward = { type?: string; item_id?: number; quantity?: number; duration_seconds?: number; code?: string; amount?: number }
type RewardRow = { day: number; group: 'daily' | 'milestones'; reward: Reward }
type Prize = { key: string; weight: number; rewards: Reward[] }
type Recipe = { index: number; materials: { item_id: number; quantity: number }[]; pool: Prize[] }
type VoucherPrize = { key: string; tier: number; weight: number; free: boolean; paid: boolean; show_id: number; pic_id: number; reward: Reward }
type Vouchers = { free_material: { item_id: number; quantity: number }; paid_material: { item_id: number; quantity: number }; ten_min_tier: number; refund_owned: boolean; pool: VoucherPrize[] }
type Definition = { vouchers?: Vouchers; pool_size?: number; without_replacement?: boolean; refund_owned?: boolean; daily?: Record<string, Reward[]>; milestones?: Record<string, Reward[]>; materials?: { item_id: number; quantity: number }[]; pool?: Prize[]; recipes?: Recipe[]; selection_count?: number; duplicate_policy?: string; starts_at?: number; ends_at?: number; compatibility_version?: string; basis?: string }
type Revision = { namespace: string; revision: number; enabled: boolean; definition: Definition }
const activities = [['checkin:default','每日签到'],['draw:epic','史诗碎片'],['draw:selected','自选碎片'],['draw:fine','精良碎片'],['draw:rare','稀有碎片'],['draw:workshop','宝物工坊']]
const blankReward = (): Reward => ({ item_id: 102111, quantity: 1 })

/** 编辑单项奖励，提供目录名称、数量和有效天数，不要求运营人员填写内部JSON。 */
function RewardEditor({ value, change, catalog, materialOnly=false, itemOnly=false }: { value: Reward; change: (value: Reward) => void; catalog: Catalog; materialOnly?: boolean; itemOnly?: boolean }) {
  const currency = value.type === 'currency'
  return <div className="flex flex-wrap items-center gap-2">
    {!materialOnly&&!itemOnly&&<select aria-label="奖励类型" className="h-9 rounded-md border bg-background px-2" value={currency?'currency':'item'} onChange={e=>change(e.target.value==='currency'?{type:'currency',code:'cn2020:GP',amount:100}:blankReward())}><option value="item">道具</option><option value="currency">货币</option></select>}
    <select aria-label="奖励资源" className="h-9 max-w-80 rounded-md border bg-background px-2" value={currency?value.code:String(value.item_id)} onChange={e=>change(currency?{...value,code:e.target.value}:{...value,item_id:Number(e.target.value)})}>
      {currency?Object.keys(catalog.currencies).filter(k=>k.startsWith('cn2020:')).map(k=><option key={k} value={k}>{k.replace('cn2020:','')}</option>):Object.entries(catalog.items).filter(([k])=>k.startsWith('cn2020:')).map(([k,item])=><option key={k} value={k.split(':')[1]}>{String(item.label)} · {k.split(':')[1]}</option>)}
    </select>
    <Input className="w-28" aria-label="奖励数量" type="number" min={1} value={currency?value.amount:value.quantity} onChange={e=>change(currency?{...value,amount:Number(e.target.value)}:{...value,quantity:Number(e.target.value)})}/>
    {!currency&&!materialOnly&&<><Input className="w-28" aria-label="有效天数，0为永久" type="number" min={0} value={(value.duration_seconds||0)/86400} onChange={e=>{ const next={...value}; if(Number(e.target.value)>0)next.duration_seconds=Number(e.target.value)*86400;else delete next.duration_seconds;change(next) }}/><span>天，0 为永久</span></>}
  </div>
}

/** 工坊券池按单项编辑以避免一次渲染136个完整资源目录；所有改动仍随整份规则预览发布。 */
function VoucherRules({ value, change, catalog }: { value: Vouchers; change: (value: Vouchers) => void; catalog: Catalog }) {
  const [index,setIndex]=useState(0)
  const selected=Math.min(index,Math.max(0,value.pool.length-1))
  const prize=value.pool[selected]
  /** 仅替换正在编辑的奖项，其他候选、权重及图标字段完整保留。 */
  const update=(fields:Partial<VoucherPrize>)=>change({...value,pool:value.pool.map((p,i)=>i===selected?{...p,...fields}:p)})
  return <section className="space-y-3 rounded-md border p-4"><h3 className="font-semibold">礼品券／宝物券抽奖</h3>
    <p>礼品池每天可免费单抽一次；十连按十次成本扣券。修改配置不恢复当日免费次数。</p>
    <div className="flex flex-wrap gap-4">{(['free','paid'] as const).map(mode=><Label key={mode}>{mode==='free'?'礼品券每次数量':'宝物券每次数量'}<Input type="number" min={1} value={value[`${mode}_material`].quantity} onChange={e=>change({...value,[`${mode}_material`]:{...value[`${mode}_material`],quantity:Number(e.target.value)}})}/></Label>)}<Label>宝物十连最低分类<Input type="number" min={1} max={5} value={value.ten_min_tier} onChange={e=>change({...value,ten_min_tier:Number(e.target.value)})}/></Label></div>
    <Label><input type="checkbox" checked={value.refund_owned} onChange={e=>change({...value,refund_owned:e.target.checked})}/> 已有永久装备退还本次券；计数材料继续累加</Label>
    <Label>奖项<select aria-label="工坊券奖项" value={selected} onChange={e=>setIndex(Number(e.target.value))}>{value.pool.map((p,i)=><option key={p.key} value={i}>{i+1} · {String(catalog.items['cn2020:'+p.reward.item_id]?.label||p.reward.item_id)}</option>)}</select></Label>
    {prize&&<div className="space-y-3"><RewardEditor itemOnly value={prize.reward} catalog={catalog} change={reward=>update({reward})}/><div className="flex flex-wrap items-center gap-4"><Label>权重<Input type="number" min={1} value={prize.weight} onChange={e=>update({weight:Number(e.target.value)})}/></Label><Label>分类<Input type="number" min={1} max={5} value={prize.tier} onChange={e=>update({tier:Number(e.target.value)})}/></Label><Label><input type="checkbox" checked={prize.free} onChange={e=>update({free:e.target.checked})}/> 礼品池</Label><Label><input type="checkbox" checked={prize.paid} onChange={e=>update({paid:e.target.checked})}/> 宝物池</Label><Button variant="ghost" onClick={()=>change({...value,pool:value.pool.filter((_,i)=>i!==selected)})}>移除此奖项</Button></div></div>}
    <Button variant="outline" onClick={()=>{const next={key:crypto.randomUUID(),tier:4,weight:1,free:true,paid:true,show_id:0,pic_id:0,reward:blankReward()};change({...value,pool:[...value.pool,next]});setIndex(value.pool.length)}}>添加工坊券奖项</Button>
  </section>
}

/** 在已有Owner工作台管理活动，展示旧版本，预览冻结后才允许发布；修改输入自动取消预览。 */
export function NativeRules({ api, report }: { api: Api; report: (value: unknown) => void }) {
  const [catalog,setCatalog]=useState<Catalog|null>(null)
  const [history,setHistory]=useState<Revision[]>([])
  const [namespace,setNamespace]=useState('checkin:default')
  const [revision,setRevision]=useState(0)
  const [enabled,setEnabled]=useState(false)
  const [days,setDays]=useState<RewardRow[]>([])
  const [materials,setMaterials]=useState<{item_id:number;quantity:number}[]>([{item_id:102111,quantity:1}])
  const [prizes,setPrizes]=useState<Prize[]>([])
  const [recipes,setRecipes]=useState<Recipe[]>([])
  const [recipeIndex,setRecipeIndex]=useState(0)
  const [sourceDefinition,setSourceDefinition]=useState<Definition>({})
  const [selection,setSelection]=useState(1)
  const [duplicates,setDuplicates]=useState('allow')
  const [starts,setStarts]=useState('')
  const [ends,setEnds]=useState('')
  const [preview,setPreview]=useState<Data|null>(null)
  const [busy,setBusy]=useState(false)
  const retry=useRef(new RetryEnvelope())
  const checkin=namespace.startsWith('checkin:')
  const workshop=namespace==='draw:workshop'
  /** 读取当前目录和版本历史；不自动创建、启用或发布任何活动。 */
  useEffect(()=>{let active=true;Promise.all([api.request<Catalog>('/api/views/catalog'),api.request<Revision[]>('/api/views/native-rules')]).then(([c,h])=>{if(active){setCatalog(c);setHistory(h)}}).catch(e=>report(String(e)));return()=>{active=false}},[api,report])
  /** 切换活动时载入最新完整定义，保留一个奖项中的多项奖励。 */
  useEffect(()=>{
    const current=history.filter(r=>r.namespace===namespace).sort((a,b)=>b.revision-a.revision)[0]
    const def=current?.definition||{}
    setSourceDefinition(def)
    setRevision(current?.revision||0);setEnabled(current?.enabled||false);setPreview(null)
    setDays((['daily','milestones'] as const).flatMap(group=>Object.entries(def[group]||{}).flatMap(([day,rewards])=>rewards.map(reward=>({group,day:Number(day),reward})))))
    const initial=def.recipes?.[0]
    setRecipes(def.recipes||[]);setRecipeIndex(initial?.index||0)
    setMaterials(initial?.materials||def.materials||[{item_id:namespace==='draw:selected'?102432:namespace==='draw:fine'?101517:namespace==='draw:rare'?101144:102111,quantity:1}])
    setPrizes(initial?.pool||def.pool||[]);setSelection(def.selection_count||1);setDuplicates(def.duplicate_policy||'allow')
    const local=(seconds?:number)=>seconds?new Date((seconds+28800)*1000).toISOString().slice(0,16):''
    setStarts(local(def.starts_at));setEnds(local(def.ends_at))
  },[namespace,history])
  /** 所有输入改变使旧预览失效，不能使用新输入提交旧版本预览。 */
  const edit=(fn:()=>void)=>{setPreview(null);fn()}
  /** 切换工坊配方前保存当前编辑草稿；发布仍一次冻结整个规则，不能漏掉其他154条配方。 */
  const chooseRecipe=(index:number)=>edit(()=>{
    const updated=recipes.map(r=>r.index===recipeIndex?{...r,materials,pool:prizes}:r)
    const next=updated.find(r=>r.index===index)
    if(next){setRecipes(updated);setRecipeIndex(index);setMaterials(next.materials);setPrizes(next.pool)}
  })
  /** 将北京时间输入转换为UTC秒；奖励配置由服务端再次验证。 */
  const definition=():Definition=>{
    // 保留未在当前表单编辑的机制，避免编辑奖励时丢失不重复抽取、自选整轮与工坊券池。
    const result:Definition={...sourceDefinition}
    delete result.starts_at;delete result.ends_at
    if(starts)result.starts_at=Date.parse(starts+':00+08:00')/1000
    if(ends)result.ends_at=Date.parse(ends+':00+08:00')/1000
    if(checkin){result.daily={};result.milestones={};for(const row of days){const map=result[row.group]!;(map[String(row.day)]??=[]).push(row.reward)}}
    else if(workshop){result.recipes=recipes.length?recipes.map(r=>r.index===recipeIndex?{...r,materials,pool:prizes}:r):[{index:0,materials,pool:prizes}];result.duplicate_policy=duplicates}
    else{result.materials=materials;result.pool=prizes;result.duplicate_policy=duplicates;if(namespace==='draw:selected')result.selection_count=selection}
    return result
  }
  /** 预览与发布分开使用幂等键；发布超时保留原键，成功后刷新历史，不重复发奖。 */
  const submit=async(publish:boolean)=>{
    setBusy(true)
    try{
      const payload:Data=publish?preview!:{namespace,expected_revision:revision,enabled,definition:definition() as unknown as Json}
      const action=publish?'native.rules.publish':'native.rules.preview'
      const meta=retry.current.prepare({action,...payload},'配置活动奖励')
      const result=await api.command(action,payload,meta);retry.current.success()
      if(publish){setPreview(null);setHistory(await api.request<Revision[]>('/api/views/native-rules'));report('活动配置已发布')}
      else{setPreview(payload);report(result)}
    }catch(error){report(String(error))}finally{setBusy(false)}
  }
  if(!catalog)return <p>读取活动配置…</p>
  return <Card><CardHeader><CardTitle>签到与抽奖奖励</CardTitle></CardHeader><CardContent className="space-y-5">
    <p>按北京时间生效。签到按月记录每日及累计奖励；初次配置默认停用。抽奖权重默认相同，可单独调整。</p>
    <fieldset disabled={busy} className="space-y-4">
      <div className="flex flex-wrap items-center gap-4"><select aria-label="活动" className="h-9 rounded-md border bg-background px-2" value={namespace} onChange={e=>setNamespace(e.target.value)}>{activities.map(([k,v])=><option key={k} value={k}>{v}</option>)}</select><Label><input type="checkbox" checked={enabled} onChange={e=>edit(()=>setEnabled(e.target.checked))}/> 发布后启用</Label><span>当前版本 {revision}</span></div>
      <div className="flex flex-wrap gap-4"><Label>开始时间（北京时间）<Input type="datetime-local" value={starts} onChange={e=>edit(()=>setStarts(e.target.value))}/></Label><Label>结束时间（留空为不限）<Input type="datetime-local" value={ends} onChange={e=>edit(()=>setEnds(e.target.value))}/></Label></div>
      {checkin?<div className="space-y-3">{days.map((row,i)=><div key={i} className="space-y-2 rounded-md border p-3"><div className="flex gap-2"><select aria-label="签到档位" value={row.group} onChange={e=>edit(()=>setDays(days.map((r,j)=>j===i?{...r,group:e.target.value as RewardRow['group']}:r)))}><option value="daily">当月第几日</option><option value="milestones">当月累计签到天数</option></select><Input className="w-24" type="number" min={1} max={31} aria-label="天数" value={row.day} onChange={e=>edit(()=>setDays(days.map((r,j)=>j===i?{...r,day:Number(e.target.value)}:r)))}/><Button variant="ghost" onClick={()=>edit(()=>setDays(days.filter((_,j)=>j!==i)))}>移除</Button></div><RewardEditor catalog={catalog} value={row.reward} change={reward=>edit(()=>setDays(days.map((r,j)=>j===i?{...r,reward}:r)))}/></div>)}<Button variant="outline" onClick={()=>edit(()=>setDays([...days,{group:'daily',day:1,reward:blankReward()}]))}>添加签到奖励</Button></div>:<>
        {workshop&&<Label>工坊配方<select aria-label="工坊配方" value={recipeIndex} onChange={e=>chooseRecipe(Number(e.target.value))}>{(recipes.length?recipes:[{index:0,materials,pool:prizes}]).map(r=><option key={r.index} value={r.index}>{r.index+1} · {String(catalog.items['cn2020:'+r.pool[0]?.rewards[0]?.item_id]?.label||'新配方')}</option>)}</select><span> 每条配方独立设置材料和产出。</span></Label>}
        <p>每次消耗的材料</p>{materials.map((row,i)=><div key={i} className="flex gap-2"><RewardEditor materialOnly catalog={catalog} value={{item_id:row.item_id,quantity:row.quantity}} change={r=>edit(()=>setMaterials(materials.map((m,j)=>j===i?{item_id:r.item_id||0,quantity:r.quantity||0}:m)))}/><Button variant="ghost" onClick={()=>edit(()=>setMaterials(materials.filter((_,j)=>j!==i)))}>移除</Button></div>)}<Button variant="outline" onClick={()=>edit(()=>setMaterials([...materials,{item_id:102111,quantity:1}]))}>添加材料</Button>
        {namespace==='draw:selected'&&<Label>自选奖项数量<Input type="number" min={1} value={selection} onChange={e=>edit(()=>setSelection(Number(e.target.value)))}/></Label>}
        <Label>重复奖励<select aria-label="重复奖励规则" value={duplicates} onChange={e=>edit(()=>setDuplicates(e.target.value))}><option value="allow">允许重复获得</option><option value="exclude_owned">排除已拥有物品</option></select></Label>
        {prizes.map((prize,i)=><div key={i} className="space-y-2 rounded-md border p-3"><div className="flex gap-2"><Input aria-label="奖项名称" value={prize.key} onChange={e=>edit(()=>setPrizes(prizes.map((p,j)=>j===i?{...p,key:e.target.value}:p)))}/><Input className="w-28" aria-label="抽取权重" type="number" min={1} value={prize.weight} onChange={e=>edit(()=>setPrizes(prizes.map((p,j)=>j===i?{...p,weight:Number(e.target.value)}:p)))}/><Button variant="ghost" onClick={()=>edit(()=>setPrizes(prizes.filter((_,j)=>j!==i)))}>移除奖项</Button></div>{prize.rewards.map((reward,k)=><RewardEditor key={k} catalog={catalog} value={reward} change={r=>edit(()=>setPrizes(prizes.map((p,j)=>j===i?{...p,rewards:p.rewards.map((v,l)=>k===l?r:v)}:p)))}/>)}<Button variant="ghost" onClick={()=>edit(()=>setPrizes(prizes.map((p,j)=>j===i?{...p,rewards:[...p.rewards,blankReward()]}:p)))}>添加组合奖励</Button></div>)}<Button variant="outline" onClick={()=>edit(()=>setPrizes([...prizes,{key:'奖项'+(prizes.length+1),weight:1,rewards:[blankReward()]}]))}>添加奖项</Button>
      </>}
      {workshop&&sourceDefinition.vouchers&&<VoucherRules catalog={catalog} value={sourceDefinition.vouchers} change={vouchers=>edit(()=>setSourceDefinition({...sourceDefinition,vouchers}))}/>}
      {!checkin&&!workshop&&<div className="flex gap-4"><Label><input type="checkbox" checked={sourceDefinition.without_replacement||false} onChange={e=>edit(()=>setSourceDefinition({...sourceDefinition,without_replacement:e.target.checked}))}/> 本轮不重复抽取</Label><Label><input type="checkbox" checked={sourceDefinition.refund_owned||false} onChange={e=>edit(()=>setSourceDefinition({...sourceDefinition,refund_owned:e.target.checked}))}/> 已有永久奖励退还材料</Label></div>}
      <div className="flex gap-3"><Button variant="outline" onClick={()=>submit(false)}>校验并预览</Button><Button disabled={!preview} onClick={()=>submit(true)}>{enabled?'发布并启用':'保存停用配置'}</Button></div>
    </fieldset>
    {preview&&<Details value={preview}/>}
  </CardContent></Card>
}
