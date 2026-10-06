import yaml
import requests
import re
import subprocess
import time
import urllib.parse
import traceback
import os
from datetime import datetime, timezone

# 1. 已经是标准 Clash YAML 的链接，直接用 Python 解析，速度极快且稳定
CLASH_YAML_URLS = [
    "https://raw.githubusercontent.com/dongchengjie/airport/main/subs/merged/tested_within.yaml",
    "https://sunmiao4458.github.io/free-proxy-airport/clash.yaml",
    "https://raw.githubusercontent.com/Ruk1ng001/freeSub/main/clash.yaml",
    "https://raw.githubusercontent.com/a2470982985/getNode/main/clash.yaml",
    "https://raw.githubusercontent.com/SnapdragonLee/SystemProxy/master/dist/clash_config.yaml"
]

# 2. 需要格式转换的链接 (Base64 / 纯文本 URI)
NEEDS_CONVERSION_URLS = [
    "https://raw.githubusercontent.com/ninjastrikers/Nexus-nodes/main/configs/all.txt"
]

IP_API_CALLS = 0
MAX_IP_API_CALLS = 40

def clean_name(name):
    if not name: return "Unknown_Node"
    name = re.sub(r'[^\w\s\u4e00-\u9fa5\-_\.\[\]\(\)\/]', '', str(name))
    return name.strip()[:50]

def is_excluded_by_name(name):
    name_upper = name.upper()
    exclude_keywords = ['中国', '大陆', 'CN', 'CHN', 'MAINLAND', '韩国', 'KR', 'KOR', 'KOREA']
    return any(keyword in name_upper or keyword in name for keyword in exclude_keywords)

def get_country_from_name(name):
    name_upper = name.upper()
    if re.search(r'\bHK\b|\bHKG\b|香港', name, re.I): return 'HK'
    if re.search(r'\bUS\b|\bUSA\b|美国', name_upper): return 'US'
    if re.search(r'\bJP\b|\bJPN\b|日本', name_upper): return 'JP'
    if re.search(r'\bSG\b|\bSGP\b|新加坡', name_upper): return 'SG'
    if re.search(r'\bUK\b|\bGBR\b|英国', name_upper): return 'UK'
    if re.search(r'\bDE\b|\bDEU\b|德国', name_upper): return 'DE'
    if re.search(r'\bCA\b|\bCAN\b|加拿大', name_upper): return 'CA'
    if re.search(r'\bFR\b|\bFRA\b|法国', name_upper): return 'FR'
    if re.search(r'\bRU\b|\bRUS\b|俄罗斯', name_upper): return 'RU'
    if re.search(r'\bNL\b|\bNLD\b|荷兰', name_upper): return 'NL'
    if re.search(r'\bTW\b|\bTWN\b|台湾', name_upper): return 'TW'
    return None

def get_country_via_ip_api(server):
    global IP_API_CALLS
    if IP_API_CALLS >= MAX_IP_API_CALLS:
        return 'OTHER'
    try:
        IP_API_CALLS += 1
        url = f"http://ip-api.com/json/{server}?fields=countryCode,status"
        resp = requests.get(url, timeout=4)
        data = resp.json()
        time.sleep(1.5) 
        if data.get('status') == 'success':
            country_code = data.get('countryCode', 'OTHER').upper()
            if country_code in ['CN', 'KR']:
                return 'EXCLUDED'
            return country_code
    except Exception:
        time.sleep(1.5)
    return 'OTHER'

def main():
    try:
        all_proxies = []

        # ==========================================
        # 步骤 1A: 直接解析已知的 Clash YAML 链接
        # ==========================================
        print("步骤 1A: 直接解析标准 Clash YAML 订阅...")
        for url in CLASH_YAML_URLS:
            try:
                print(f"  获取: {url}")
                resp = requests.get(url, timeout=15, headers={'User-Agent': 'ClashMeta/1.18.8'})
                resp.raise_for_status()
                
                data = yaml.safe_load(resp.text)
                if isinstance(data, dict) and 'proxies' in data and isinstance(data['proxies'], list):
                    all_proxies.extend(data['proxies'])
                    print(f"  成功提取 {len(data['proxies'])} 个节点")
                else:
                    print(f"  格式异常，未找到 'proxies' 列表")
            except Exception as e:
                print(f"  获取或解析失败: {e}")

        # ==========================================
        # 步骤 1B: 使用 subconverter 转换非标准链接
        # ==========================================
        if NEEDS_CONVERSION_URLS:
            print("\n步骤 1B: 启动 subconverter 转换非标准订阅...")
            sub_process = subprocess.Popen(['./subconverter_exec', '-d'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3) # 等待服务启动

            for url in NEEDS_CONVERSION_URLS:
                try:
                    print(f"  获取待转换内容: {url}")
                    resp = requests.get(url, timeout=15, headers={'User-Agent': 'ClashMeta/1.18.8'})
                    resp.raise_for_status()
                    
                    # 写入本地临时文件
                    with open('temp_raw_subscription.txt', 'w', encoding='utf-8') as f:
                        f.write(resp.text)
                    
                    # 使用 file:// 协议让 subconverter 读取本地文件，彻底避免 URL 长度限制
                    file_url = urllib.parse.quote(f"file://{os.path.abspath('temp_raw_subscription.txt')}")
                    api_url = f"http://127.0.0.1:25500/sub?target=clash&url={file_url}&insert=false&emoji=false&sort=false&scv=true"
                    
                    print(f"  正在请求 subconverter 转换...")
                    conv_resp = requests.get(api_url, timeout=60)
                    conv_resp.raise_for_status()
                    
                    converted_data = yaml.safe_load(conv_resp.text)
                    if isinstance(converted_data, dict) and 'proxies' in converted_data and isinstance(converted_data['proxies'], list):
                        all_proxies.extend(converted_data['proxies'])
                        print(f"  subconverter 成功转换并提取 {len(converted_data['proxies'])} 个节点")
                        
                except Exception as e:
                    print(f"  转换失败: {e}")
                finally:
                    # 清理临时文件
                    if os.path.exists('temp_raw_subscription.txt'):
                        os.remove('temp_raw_subscription.txt')

            # 停止 subconverter
            sub_process.terminate()
            sub_process.wait()

        if not all_proxies:
            print("\n未找到任何有效节点，退出。")
            exit(1)
            
        print(f"\n合并后节点总数: {len(all_proxies)}")

        # ==========================================
        # 步骤 2: 节点清洗、去重与国家识别
        # ==========================================
        print("\n步骤 2: 执行节点清洗、去重与国家识别...")
        seen = set()
        unique_proxies = []
        excluded_count = 0
        ip_api_checked_count = 0
        
        for p in all_proxies:
            if not isinstance(p, dict): continue
            
            name = clean_name(p.get('name', ''))
            p['name'] = name
            
            if is_excluded_by_name(name):
                excluded_count += 1
                continue
                
            country = get_country_from_name(name)
            if country is None:
                ip_api_checked_count += 1
                country = get_country_via_ip_api(p.get('server', ''))
                
            if country == 'EXCLUDED':
                excluded_count += 1
                continue
                
            if country is None or country == 'OTHER':
                country = 'OTHER'

            server = str(p.get('server', ''))
            port = str(p.get('port', ''))
            ptype = str(p.get('type', ''))
            key = f"{name}|{server}|{port}|{ptype}"
            
            if key not in seen:
                seen.add(key)
                unique_proxies.append(p)

        print(f"  已排除 (中国/韩国) 节点: {excluded_count} 个")
        print(f"  触发 ip-api.com 检测次数: {ip_api_checked_count} 次")
        print(f"  最终有效节点总数: {len(unique_proxies)}")

        if not unique_proxies:
            print("过滤后无有效节点，退出。")
            exit(1)

        # ==========================================
        # 步骤 3: mihomo 多线程并发测速
        # ==========================================
        print("\n步骤 3: 启动 mihomo 进行多线程并发测速...")
        temp_config = {
            'mixed-port': 7890, 'allow-lan': True, 'log-level': 'warning',
            'external-controller': '127.0.0.1:9090', 'proxies': unique_proxies,
            'proxy-groups': [{'name': 'TEST-GROUP', 'type': 'url-test', 'proxies': [p['name'] for p in unique_proxies], 'url': 'http://www.gstatic.com/generate_204', 'interval': 300}]
        }
        with open('temp.yaml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(temp_config, f, allow_unicode=True, sort_keys=False)

        process = subprocess.Popen(['./mihomo', '-d', '.', '-f', 'temp.yaml'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(6) 

        try:
            requests.get('http://127.0.0.1:9090/proxies/TEST-GROUP/delay?timeout=5000&url=http://www.gstatic.com/generate_204', timeout=120)
        except Exception as e:
            print(f"  测速请求异常: {e}")

        delay_map = {}
        try:
            res = requests.get('http://127.0.0.1:9090/proxies', timeout=10).json()
            test_group = res['proxies'].get('TEST-GROUP', {})
            for proxy in test_group.get('all', []):
                delay_map[proxy['name']] = proxy.get('history', [])[-1]['delay'] if proxy.get('history') else 99999
        except Exception as e:
            print(f"  获取测速结果失败: {e}")
            delay_map = {p['name']: 99999 for p in unique_proxies}

        process.terminate()
        process.wait()
        print("mihomo 核心已停止")

        # ==========================================
        # 步骤 4: 按国家筛选 Top 20 并生成最终配置
        # ==========================================
        print("\n步骤 4: 按国家筛选 Top 20 低延迟节点:")
        country_groups = {}
        for p in unique_proxies:
            delay = delay_map.get(p['name'], 99999)
            if delay == 0 or delay >= 5000: continue
            country = get_country_from_name(p['name']) or 'OTHER'
            if country not in country_groups: country_groups[country] = []
            country_groups[country].append((p, delay))

        final_proxies = []
        country_pools = {}
        for country, items in country_groups.items():
            items.sort(key=lambda x: x[1])
            top_20 = items[:20]
            for p, delay in top_20: final_proxies.append(p)
            country_pools[f"{country}-POOL"] = [p['name'] for p, _ in top_20]
            print(f"  {country}: 保留 {len(top_20)} 个 (最低延迟: {top_20[0][1]}ms)")

        print(f"\n最终筛选出 {len(final_proxies)} 个高质量节点")

        pool_names = sorted(list(country_pools.keys()))
        ai_pool_proxies = [p['name'] for p in final_proxies if re.search(r'\bUS\b|\bSG\b|\bCA\b|AI', p['name'], re.I)] or [p['name'] for p in final_proxies]

        final_config = {
            'mixed-port': 7890, 'allow-lan': True, 'mode': 'rule', 'log-level': 'info',
            'ipv6': True, 'unified-delay': True, 'tcp-concurrent': True, 'global-client-fingerprint': 'chrome',
            'generated-by': 'github-actions-auto-merge-v8', 'generated-at': datetime.now(timezone.utc).isoformat(),
            'proxies': final_proxies,
            'proxy-groups': [{'name': 'AUTO-FAST', 'type': 'url-test', 'proxies': [p['name'] for p in final_proxies], 'url': 'http://www.gstatic.com/generate_204', 'interval': 120}],
            'rules': ['DOMAIN-SUFFIX,openai.com,AI-POOL', 'DOMAIN-SUFFIX,chatgpt.com,AI-POOL', 'DOMAIN-SUFFIX,claude.ai,AI-POOL', 'DOMAIN-SUFFIX,anthropic.com,AI-POOL', 'GEOIP,CN,DIRECT', 'MATCH,PROXY']
        }
        for pool_name in pool_names:
            final_config['proxy-groups'].append({'name': pool_name, 'type': 'url-test', 'proxies': country_pools[pool_name], 'url': 'http://www.gstatic.com/generate_204', 'interval': 120})
        final_config['proxy-groups'].extend([
            {'name': 'AI-POOL', 'type': 'url-test', 'proxies': ai_pool_proxies[:50], 'url': 'http://www.gstatic.com/generate_204', 'interval': 120},
            {'name': 'FALLBACK', 'type': 'fallback', 'proxies': ['AUTO-FAST'] + pool_names, 'url': 'http://www.gstatic.com/generate_204', 'interval': 120},
            {'name': 'PROXY', 'type': 'select', 'proxies': ['AUTO-FAST', 'FALLBACK'] + pool_names}
        ])

        with open('clash.yaml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(final_config, f, allow_unicode=True, sort_keys=False, width=1000)

        print("成功生成 clash.yaml，等待 Git 提交...")

    except Exception as e:
        print("\n" + "="*50)
        print("脚本执行失败！详细错误信息如下：")
        traceback.print_exc()
        print("="*50)
        exit(1)

if __name__ == '__main__':
    main()
