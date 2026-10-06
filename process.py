import yaml
import requests
import re
import subprocess
import time
import urllib.parse
import traceback
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ALL_URLS = [
    "https://raw.githubusercontent.com/dongchengjie/airport/main/subs/merged/tested_within.yaml",
    "https://sunmiao4458.github.io/free-proxy-airport/clash.yaml",
    "https://raw.githubusercontent.com/Ruk1ng001/freeSub/main/clash.yaml",
    "https://raw.githubusercontent.com/a2470982985/getNode/main/clash.yaml",
    "https://raw.githubusercontent.com/SnapdragonLee/SystemProxy/master/dist/clash_config.yaml",
    "https://raw.githubusercontent.com/ninjastrikers/Nexus-nodes/main/configs/all.txt"
]

IP_API_CALLS = 0
MAX_IP_API_CALLS = 40
UDP_ONLY_PROTOCOLS = {'hysteria', 'hysteria2', 'tuic', 'snell'}

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

def tcp_ping(server, port, timeout=1.5):
    try:
        with socket.create_connection((server, int(port)), timeout=timeout):
            return True
    except Exception:
        return False

def sanitize_yaml_text(text):
    """防护 1: 移除导致 Python yaml 解析失败的特殊控制字符 (如 \x009f)"""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

def main():
    try:
        all_proxies = []
        raw_text_for_subconverter = ""
        reality_fixed_count = 0

        print("步骤 1: 智能下载与解析订阅源...")
        for url in ALL_URLS:
            try:
                resp = requests.get(url, timeout=15, headers={'User-Agent': 'ClashMeta/1.18.8'})
                resp.raise_for_status()
                text = resp.text
                
                clean_text = sanitize_yaml_text(text)
                try:
                    data = yaml.safe_load(clean_text)
                    if isinstance(data, dict) and 'proxies' in data and isinstance(data['proxies'], list):
                        all_proxies.extend(data['proxies'])
                        print(f"  Python 成功提取 {len(data['proxies'])} 个节点: {url.split('/')[-1]}")
                        continue 
                except yaml.YAMLError as e:
                    print(f"  Python 解析失败: {str(e)[:60]}... 将交由 subconverter 处理")
                
                raw_text_for_subconverter += clean_text + "\n---\n"
            except Exception as e:
                print(f"  获取失败: {url.split('/')[-1]} - {e}")

        if raw_text_for_subconverter.strip():
            print("\n启动 subconverter 清洗并转换非标准格式...")
            sub_process = subprocess.Popen(['./subconverter_exec', '-d'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
            with open('temp_raw.txt', 'w', encoding='utf-8') as f:
                f.write(raw_text_for_subconverter)
            
            file_url = urllib.parse.quote(f"file://{os.path.abspath('temp_raw.txt')}")
            api_url = f"http://127.0.0.1:25500/sub?target=clash&url={file_url}&insert=false&emoji=false&sort=false&scv=true"
            
            try:
                conv_resp = requests.get(api_url, timeout=120)
                if conv_resp.status_code != 200:
                    print(f"  subconverter 返回错误状态码: {conv_resp.status_code}")
                else:
                    converted_data = yaml.safe_load(conv_resp.text)
                    if isinstance(converted_data, dict) and 'proxies' in converted_data and isinstance(converted_data['proxies'], list):
                        all_proxies.extend(converted_data['proxies'])
                        print(f"  subconverter 成功提取 {len(converted_data['proxies'])} 个节点")
            except Exception as e:
                print(f"  subconverter 请求异常: {e}")
            finally:
                if os.path.exists('temp_raw.txt'): os.remove('temp_raw.txt')
                sub_process.terminate()
                sub_process.wait()

        if not all_proxies:
            print("\n未找到任何有效节点，退出。")
            exit(1)

        print(f"\n合并后节点总数: {len(all_proxies)}")

        # ==========================================
        # 步骤 2: 节点清洗、去重、校验与国家识别
        # ==========================================
        print("\n步骤 2: 执行节点清洗、去重与国家识别...")
        seen = set()
        unique_proxies = []
        excluded_count = 0
        invalid_count = 0
        ip_api_checked_count = 0
        
        for p in all_proxies:
            if not isinstance(p, dict): continue
            
            # 防护 2: 严格的字段校验，丢弃真正缺少核心字段的废节点
            if not all(k in p and p[k] for k in ['name', 'server', 'port', 'type']):
                invalid_count += 1
                continue
                
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
            ptype = str(p.get('type', '')).lower()
            
            # 防护 3: 智能修复 REALITY 节点的 short-id，保留优质节点并防止 mihomo 崩溃
            if ptype == 'vless' and 'reality-opts' in p and isinstance(p['reality-opts'], dict):
                short_id_raw = p['reality-opts'].get('short-id', '')
                
                # 处理 YAML 将 '0123' 错误解析为整数 123 的情况
                if short_id_raw is None:
                    short_id_str = ""
                else:
                    short_id_str = str(short_id_raw).strip()
                
                # 严格校验：必须是空字符串，或者 2-16 位的【偶数长度】十六进制字符串
                is_valid = (short_id_str == "") or (re.match(r'^[0-9a-fA-F]+$', short_id_str) and len(short_id_str) % 2 == 0 and 2 <= len(short_id_str) <= 16)
                
                if is_valid:
                    p['reality-opts']['short-id'] = short_id_str
                else:
                    # 终极兜底：如果 short-id 损坏，降级为空字符串 ""。
                    # 在 Xray/mihomo 规范中，"" 表示不校验 short-id，节点依然可以尝试连接，且 100% 不会导致 fatal 崩溃。
                    p['reality-opts']['short-id'] = ""
                    reality_fixed_count += 1

            key = f"{name}|{server}|{port}|{ptype}"
            
            if key not in seen:
                seen.add(key)
                unique_proxies.append(p)

        print(f"  已排除 (中国/韩国) 节点: {excluded_count} 个")
        print(f"  已丢弃真正缺失字段的废节点: {invalid_count} 个")
        print(f"  已智能修复损坏的 REALITY short-id: {reality_fixed_count} 个 (降级为不校验，防止崩溃)")
        print(f"  触发 ip-api.com 检测次数: {ip_api_checked_count} 次 (已限速保护)")
        print(f"  待测速节点总数: {len(unique_proxies)}")

        if not unique_proxies:
            print("过滤后无有效节点，退出。")
            exit(1)

        # ==========================================
        # 步骤 2.5: 协议感知型智能初筛
        # ==========================================
        print("\n步骤 2.5: 启动智能初筛 (UDP协议直接放行，TCP协议极速Ping)...")
        alive_proxies = []
        
        def check_proxy(p):
            ptype = str(p.get('type', '')).lower()
            server = p.get('server', '')
            port = p.get('port', 80)
            
            if ptype in UDP_ONLY_PROTOCOLS:
                return True
            if not server or server in ['127.0.0.1', 'localhost', '0.0.0.0']:
                return False
            try:
                return tcp_ping(server, port, timeout=1.5)
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=100) as executor:
            future_to_proxy = {executor.submit(check_proxy, p): p for p in unique_proxies}
            for future in as_completed(future_to_proxy):
                p = future_to_proxy[future]
                if future.result():
                    alive_proxies.append(p)

        print(f"  初筛完成！剔除死节点 {len(unique_proxies) - len(alive_proxies)} 个，剩余 {len(alive_proxies)} 个节点进入 mihomo 真实测速。")

        # ==========================================
        # 步骤 3: mihomo 真实协议测速
        # ==========================================
        print("\n步骤 3: 启动 mihomo 进行多线程真实协议测速...")
        temp_config = {
            'mixed-port': 7890, 'allow-lan': True, 'log-level': 'warning',
            'external-controller': '127.0.0.1:9090', 'proxies': alive_proxies,
            'proxy-groups': [{'name': 'TEST-GROUP', 'type': 'url-test', 'proxies': [p['name'] for p in alive_proxies], 'url': 'http://www.gstatic.com/generate_204', 'interval': 300}]
        }
        with open('temp.yaml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(temp_config, f, allow_unicode=True, sort_keys=False)

        process = subprocess.Popen(
            ['./mihomo', '-d', '.', '-f', 'temp.yaml'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        time.sleep(5) 

        if process.poll() is not None:
            stdout, stderr = process.communicate()
            print(f"\nmihomo 进程意外退出！")
            print(f"错误日志 (stderr):\n{stderr}")
            print(f"标准输出 (stdout):\n{stdout}")
            exit(1)

        try:
            requests.get('http://127.0.0.1:9090/proxies/TEST-GROUP/delay?timeout=5000&url=http://www.gstatic.com/generate_204', timeout=60)
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
            delay_map = {p['name']: 99999 for p in alive_proxies}

        process.terminate()
        process.wait()
        print("mihomo 核心已停止")

        # ==========================================
        # 步骤 4: 按国家筛选 Top 20 并生成最终配置
        # ==========================================
        print("\n步骤 4: 按国家筛选 Top 20 低延迟节点:")
        country_groups = {}
        for p in alive_proxies:
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
            'generated-by': 'github-actions-auto-merge-v15', 'generated-at': datetime.now(timezone.utc).isoformat(),
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
