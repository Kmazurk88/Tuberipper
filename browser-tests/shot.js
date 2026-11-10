const { chromium } = require('playwright');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:520,height:270}});await p.goto('file:///work/icon.html');await p.screenshot({path:'/out/icon.png'});await b.close();})();
