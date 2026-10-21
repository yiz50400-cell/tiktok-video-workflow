// Small real app-server smoke; credentials and raw protocol never logged.
const fs=require('node:fs');const path=require('node:path');const os=require('node:os');
const {execFileSync}=require('node:child_process');const {Codex}=require('../desktop/codex.cjs');
const root=path.resolve(__dirname,'..');const python=path.join(root,'.venv/bin/python');
const relay=JSON.parse(execFileSync(python,[path.join(root,'scripts/desktop_config.py'),path.join(os.homedir(),'.codex/config.toml')],{encoding:'utf8'}));
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'workflow-app-server-'));
const bridge=new Codex({executable:path.join(os.homedir(),'.local/bin/codex'),home:path.join(directory,'home'),
  workspace:directory,settings:{read:()=>({baseUrl:relay.base_url,model:relay.model,key:relay.key})},python,engine:root});
const report={codexVersion:'0.152.0',model:relay.model,transport:'stdio',checks:[]};
async function probe(text,token,requireTool){
  const started=Date.now();let response='',tools=0;
  let finish;
  const done=new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('timeout')),90000);
    const listener=e=>{
      if(e.type==='delta')response+=e.text;
      if(e.type==='message')response=e.text;
      if(e.type==='tool'&&e.exitCode===0&&e.output?.includes(token))tools++;
      if(e.type==='request')bridge.decision(e.id,e.method==='item/tool/requestUserInput'?{}:false);
      if(e.type==='completed'){clearTimeout(timer);bridge.off('event',listener);resolve(e.status);}
    };
    bridge.on('event',listener);finish=()=>{clearTimeout(timer);bridge.off('event',listener);};
  });
  try{
    await bridge.send('job_desktopprobe',text);const status=await done;
    return {status:status==='completed'&&response.trim()===token&&(!requireTool||tools>0)?'passed':'failed',
      seconds:(Date.now()-started)/1000,actualToolSuccesses:tools};
  }finally{finish();}
}
(async()=>{
  try{
    report.checks.push(await probe('本轮仅进行连接检查。不要读取文档、任务或使用工具。请只回复 DESKTOP_STDIO_OK。','DESKTOP_STDIO_OK',false));
    fs.writeFileSync(path.join(directory,'probe.txt'),'DESKTOP_LOCAL_TOOL_OK');
    report.checks.push(await probe('本轮仅进行本地工具检查。用 shell 工具读取当前目录 probe.txt，不读其他文件、不查询任务、不查看环境变量，然后原样回复文件内容。','DESKTOP_LOCAL_TOOL_OK',true));
  }catch{report.error='app_server_probe_failed';}
  finally{
    bridge.close();fs.rmSync(directory,{recursive:true,force:true});
    const target=path.join(root,'.runtime','desktop-validation-2026-10-14');fs.mkdirSync(target,{recursive:true});
    fs.writeFileSync(path.join(target,'codex-app-server.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report,null,2));process.exitCode=report.checks.length===2&&report.checks.every(c=>c.status==='passed')?0:1;
  }
})();
