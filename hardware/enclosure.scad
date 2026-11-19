// 陪聊硬件外壳 —— 微雪 ESP32-S3-AUDIO-Board 专用
// 大按钮对讲机造型：外壳顶部一个大按钮盖，直接压在板载小按键上（不用接线/焊接）
//
// 尺寸来源：商品详情页的实拍标注图，是台阶形结构——
// 上面是较宽的圆形 PCB（58mm），下面是较窄的喇叭机身（46mm），中间三颗铜柱支撑，
// 底部另有 5.1mm 高的小凸脚。这些数字是官方标注的，可信；
// 但按键/麦克风/USB口的"具体角度位置"是我根据丝印图布局推测的，不是量出来的角度，
// 东西到手后麻烦帮忙确认一下方向对不对（比如"按键是不是靠近USB口那一侧"），
// 不对的话告诉我实际方位，我改 button_angle / mic_angle 这几个角度值就行，不用重新设计。

// ===================== 官方标注尺寸（可信）=====================
top_disc_diameter   = 58;    // 顶部 PCB 圆盘直径
body_diameter        = 46;    // 下方喇叭机身直径（比顶盘窄一圈，台阶形）
total_height          = 43.7;  // 从机身底部到 PCB 顶面的总高
foot_height            = 5.1;   // 最底部小凸脚高度（额外增加，不含在 total_height 内）
top_rim_thickness     = 1.55;  // 顶盘边缘厚度

// ===================== 推测位置（东西到手后建议确认）=====================
usb_c_angle      = 0;    // USB-C 口所在角度（0度=右方向），照片上在顶盘边缘
usb_c_width      = 10;
usb_c_height     = 4;
usb_c_from_top   = 6;    // USB口中心距离PCB顶面的深度

button_angle     = 60;   // 5颗小按键（RESET/BOOT/K1/K2/K3）所在弧区的中心角度，
                          // 大按钮盖的顶柱要对准其中的 BOOT 键——固件里"按住说话"
                          // 绑定的就是 BOOT（GPIO0），K1/K2/K3 目前没接入任何逻辑，
                          // 千万别对错键，压上去也没反应
user_button_dia  = 5;    // 板载小按键本身直径

mic1_angle       = 220;  // 双麦克风阵列，丝印图上在按键对面靠排针一侧，两颗对称分布
mic2_angle       = 260;
mic_radius       = 24;   // 麦克风离圆心的距离（顶盘半径29mm，麦克风在边缘内侧）
mic_hole_dia     = 1.5;

speaker_hole_dia = 2.5;  // 喇叭出声孔阵列，孔本身直径
speaker_zone_dia = body_diameter - 8;  // 出声孔分布区域直径，比机身直径小一圈留边

// ===================== 外壳设计参数（自由调整）=====================
wall_thickness   = 2.2;
clearance        = 1.2;   // 板子/机身和内壁的间隙

case_top_diameter  = top_disc_diameter + 2*(wall_thickness + clearance);
case_body_diameter = body_diameter + 2*(wall_thickness + clearance);
case_total_height  = total_height + foot_height + wall_thickness + 8; // +8 给顶部大按钮机构留空间
top_section_height = 12; // 顶盘那一小截的高度（PCB本身很薄，这段主要是壳体过渡）

big_button_diameter = 60;    // 大按钮直径，比顶盘还略大一点，方便一巴掌拍下去
big_button_travel   = 3;
plunger_diameter    = user_button_dia + 2;

// ================================================================================
// 外壳主体：台阶形——上宽下窄，跟真实设备形状一致
module case_body() {
    difference() {
        union() {
            // 上段：宽的顶盘部分
            cylinder(d=case_top_diameter, h=top_section_height, $fn=150);
            // 下段：窄的喇叭机身部分
            translate([0, 0, top_section_height])
                cylinder(d=case_body_diameter, h=case_total_height - top_section_height, $fn=150);
        }

        // 挖空内部
        translate([0, 0, wall_thickness])
            union() {
                cylinder(d=case_top_diameter - 2*wall_thickness, h=top_section_height, $fn=150);
                translate([0, 0, top_section_height])
                    cylinder(d=case_body_diameter - 2*wall_thickness, h=case_total_height, $fn=150);
            }

        // USB-C 开孔
        translate([
            (case_top_diameter/2) * cos(usb_c_angle),
            (case_top_diameter/2) * sin(usb_c_angle),
            usb_c_from_top
        ])
            rotate([0, 0, usb_c_angle])
                rotate([0, 90, 0])
                    linear_extrude(height=wall_thickness*3, center=true)
                        square([usb_c_height, usb_c_width], center=true);

        // 麦克风孔 x2
        translate([mic_radius*cos(mic1_angle), mic_radius*sin(mic1_angle), -1])
            cylinder(d=mic_hole_dia, h=wall_thickness+2, $fn=12);
        translate([mic_radius*cos(mic2_angle), mic_radius*sin(mic2_angle), -1])
            cylinder(d=mic_hole_dia, h=wall_thickness+2, $fn=12);

        // 大按钮顶柱穿过顶面的孔——没有这个孔，外面的大按钮顶柱够不到里面的小按键
        translate([
            (top_disc_diameter/2 - 8) * cos(button_angle),
            (top_disc_diameter/2 - 8) * sin(button_angle),
            -1
        ])
            cylinder(d=plunger_diameter + 0.6, h=wall_thickness+2, $fn=30); // +0.6 留一点滑动间隙

        // 底部喇叭出声孔阵列
        for (angle = [0:24:359]) {
            translate([
                (speaker_zone_dia/2) * cos(angle),
                (speaker_zone_dia/2) * sin(angle),
                case_total_height - wall_thickness - 1
            ])
                cylinder(d=speaker_hole_dia, h=wall_thickness+2, $fn=12);
        }
        translate([0, 0, case_total_height - wall_thickness - 1])
            cylinder(d=speaker_hole_dia, h=wall_thickness+2, $fn=12); // 中心也开一个
    }

    // 底部小凸脚，垫高一点让下面出声孔不贴死在桌面上
    // （z 方向故意往主体里嵌 0.5mm，保证跟主体真正融合，不是零厚度贴面——
    // 贴面在打印时容易在接缝处出现缝隙/强度弱点）
    overlap = 0.5;
    for (a = [60, 180, 300]) {
        translate([16*cos(a), 16*sin(a), case_total_height - overlap])
            cylinder(d=6, h=foot_height + overlap, $fn=24);
    }
}

// 大按钮盖：扣在外壳顶面外侧（z=0 往外/负方向凸出），中心有根柱子穿过顶面的孔，
// 一路戳到里面板载的小按键（K1那颗）。盖子本体在外面，顶柱在里面，z=0 是分界。
//
// 蘑菇头是"球冠"（球面切一个矮帽子），不是直接拿球和圆柱做交集——
// 之前那版算错了，球半径比按钮本身还大很多，圆柱完全没被球面裁到，做出来是个平饼。
// 球冠公式：半径 r_base（帽子底面半径）、帽高 cap_height，则球本身半径
// R = (r_base² + cap_height²) / (2·cap_height)，球心下移 (R - cap_height) 再切上半部分。
dome_r_base = big_button_diameter / 2;
dome_cap_height = 12;  // 蘑菇头鼓起的高度，越大按钮摸起来越圆润凸出
dome_sphere_r = (dome_r_base*dome_r_base + dome_cap_height*dome_cap_height) / (2*dome_cap_height);

module big_button() {
    union() {
        // 蘑菇头圆顶——从 z=0 往外（负方向）鼓出来，摸起来是个大圆包
        mirror([0, 0, 1])
            intersection() {
                translate([0, 0, -(dome_sphere_r - dome_cap_height)])
                    sphere(r=dome_sphere_r, $fn=120);
                translate([0, 0, dome_cap_height/2])
                    cube([big_button_diameter*1.2, big_button_diameter*1.2, dome_cap_height], center=true);
            }
        // 顶柱——从 z=0 往里（正方向）伸进外壳，穿过顶面开的孔，压到小按键上
        translate([
            (top_disc_diameter/2 - 8) * cos(button_angle),
            (top_disc_diameter/2 - 8) * sin(button_angle),
            0
        ])
            cylinder(d=plunger_diameter, h=wall_thickness + 4, $fn=30);
    }
}

// 预览：外壳 + 大按钮盖（半透明橙色，卡在顶面外侧），方便检查顶柱是否对准按键孔
case_body();
color("orange", 0.7) big_button();

// ================================================================================
// 使用说明：
// 1. 用 OpenSCAD（免费开源，openscad.org 下载）打开本文件看 3D 预览
// 2. 东西到手后，重点确认三件事，不对就改对应角度变量：
//    a. USB-C口实际方向 —— 改 usb_c_angle
//    b. 想用哪颗按键做"大按钮"（建议用 K1，最独立好操作）、它的角度 —— 改 button_angle
//    c. 双麦克风孔位置 —— 改 mic1_angle / mic2_angle（这个其实开孔大一圈也没关系，主要别被完全挡住）
// 3. 确认预览没问题后，把下面两行改成只留一行注释掉另一行，分两次导出 STL：
//    - 只导出 case_body()（外壳主体）
//    - 只导出 big_button()（大按钮盖，单独打印后再扣上去）
// 4. 两个 STL 发去"3D打印代打"店铺，PLA 或 PETG 材料都行，不需要特殊工艺
