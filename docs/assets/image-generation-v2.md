# 第二轮界面图片来源与生成记录

用户最终选择沿用深色蓝灰与暖金主题，粉白试稿未进入发布资源。全部AI图使用内置 ImageGen，非CLI/API fallback。项目中的WebP仅进行格式编码压缩，不裁剪、不另行绘制人物；原始生成PNG保留在本机ImageGen输出目录。

## 人物参考

- 艾米、芭比：[2013年角色报道](https://nz.17173.com/news/09182013/094133489.shtml)，参考图分别为文章的`20130918094309746.jpg`、`20130918094309993.jpg`；已实际查看，确认短棕发/红框眼镜/猫耳耳机及金发/兔耳/红羽领。
- 万飞、万阳：[角色图鉴](https://nz.17173.com/info/role_all.shtml)，实际查看的`20120315114955412.jpg`、`20120315114955538.jpg`。
- 四张图鉴配图本地保存，来源与稳定ID记录于`tools/build_item_presentation.py`。不将跨版本同名道具自动映射成准确图；101365/101366等版本仍标分类示意。

## 最终素材与提示词

### `/assets/hero-duo-v3.webp`

最初以两张原图作为人物参考，生成成年艾米与芭比右侧同框、左侧留白的精细二次元主视觉。最终调色编辑提示词：

> Edit the supplied NZ Revival dual-character hero illustration for a premium dark game launcher. Preserve the two anime adult operatives, their faces, recognizable Amy short brown bob/red eyeglasses/cat-ear headphones/blue sailor uniform, Barbie blonde updo/black rabbit ears/red feather collar/black tailored operative suit, their pose and the same wide composition. Change the environment and overall lighting from bright pink-white to the existing launcher theme: deep midnight navy, steel blue, subtle warm champagne gold highlights. A moody futuristic glass conservatory at blue hour, distant city skyline, elegant navy architecture and a few drifting blue/gold light particles. LEFT 42% must be calm DARK NAVY negative space with extremely low detail and high contrast for pale website text, with a subtle gradient blending into the character side. Characters on RIGHT 58%, softly illuminated faces and crisp fine linework, beautiful refined anime style. Show all ears fully within frame with a little top padding. No pink-dominant or white background. No text, logo, watermark, UI, or extra people. Preserve identity and clothes, tasteful nonsexual framing.

### `/assets/items/equipment-category-v3.webp`

> Edit only the color palette, background and lighting of the supplied generic equipment crate illustration to match an existing dark game launcher. Keep the same elegant equipment case, crystal, dog tag, centered composition, object silhouette and premium 3D asset illustration style. Replace pink/ivory with gunmetal navy and silver case materials, pale blue crystal, and subtle champagne gold highlights. Deep midnight blue studio background with soft blue rim light; make the object readable at small thumbnail sizes. No text, logo, watermark, UI or people. This remains a clearly generic equipment category illustration, not a specific game item.

### `/assets/items/primary-category-v3.webp`

> Use case: stylized-concept. Square 1:1 generic ASSAULT RIFLE category illustration for a premium dark game inventory card, not a specific licensed game weapon. A single fictional gunmetal tactical carbine viewed in profile at a gentle diagonal, silver metal and subtle blue edge lights with tiny champagne gold mechanical details. Entire rifle within frame, centered with ample padding, clean silhouette readable at 180px. Deep midnight navy studio backdrop, restrained dramatic rim lighting, refined realistic 3D collectible game icon. No ammunition firing, no people, no text, no lettering, no watermark, no logo, no card UI. Match an elegant steel-blue-and-gold launcher aesthetic.

## 覆盖与边界

目录共4323项；准确图4项，分类示意4319项。主武器依据既有槽位使用主武器分类示意，其余使用装备分类示意。每张示意图在UI明确标注；缺准确图的逐项清单为`item-image-missing.csv`。原游戏图标资源不在本机，不能把生成图伪称为4323项原图已补齐。
