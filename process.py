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
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

def build_proxy_groups(country_pools, active_proxies, untested_proxies, ai_pool_limit):
    """通用策略组构建函数，确保 all-clash.yaml 和 clash.yaml 结构完全一致"""
    pool_names = sorted(list(country_pools.keys()))
    groups = []
    
    # 1. AUTO-FAST (仅包含通过测速的活跃节点)
    groups.append({
        'name': 'AUTO-FAST',
        'type': 'url-test',
        'proxies': [p['name'] for p in active_proxies],
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 120
    })
    
    # 2. 各个国家 POOL
    for pool_name in pool_names:
        groups.append({
            'name': pool_name,
            'type': 'url-test',
            'proxies': country_pools[pool_name],
            'url': 'http://www.gstatic.com/generate_204',
            'interval': 120
        })
        
    # 3. UNTESTED (格式异常) - 如果有，则单独分组
    if untested_proxies:
        groups.append({
            'name': 'UNTESTED (格式异常)',
            'type': 'select',
            'proxies': [p['name'] for p in untested_proxies]
        })
        
    # 4. AI-POOL (优先美/新/加)
    ai_pool_proxies = [p['name'] for p in active_proxies if re.search(r'\bUS\b|\bSG\b|\bCA\b|AI', p['name'], re.I)]
    if not ai_pool_proxies:
        ai_pool_proxies = [p['name'] for p in active_proxies]
        
    groups.append({
        'name': 'AI-POOL',
        'type': 'url-test',
        'proxies': ai_pool_proxies[:ai_pool_limit],
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 120
    })
    
    # 5. FALLBACK
    fallback_proxies = ['AUTO-FAST'] + pool_names
    groups.append({
        'name': 'FALLBACK',
        'type': 'fallback',
        'proxies': fallback_proxies,
        'url': 'http://www.gstatic.com/generate_204',
        'interval': 120
    })
    
    # 6. PROXY (手动选择)
    proxy_proxies = ['AUTO-FAST', 'FALLBACK'] + pool_names
    if untested_proxies:
        proxy_proxies.append('UNTESTED (格式异常)')
        
    groups.append({
        'name': 'PROXY',
        'type': 'select',
        'proxies': proxy_proxies
    })
    
    return groups

def main():
    try:
        all_proxies = []
        raw_text_for_subconverter = ""

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

        print("\n步骤 2: 执行节点清洗、去重与国家识别...")
        seen = set()
        unique_proxies = []
        untested_proxies = []  # 专门用于存放格式异常的节点
        excluded_count = 0
        invalid_count = 0
        ip_api_checked_count = 0
        untested_count = 0
        
        for p in all_proxies:
            if not isinstance(p, dict): continue
            
            # 1. 基础字段校验
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
            
            # 2. REALITY short-id 严格校验与隔离分流
            if ptype == 'vless' and 'reality-opts' in p and isinstance(p['reality-opts'], dict):
                sid_raw = p['reality-opts'].get('short-id', '')
                sid_str = str(sid_raw).strip() if sid_raw is not None else ""
                
                # 校验规则：必须是空字符串，或者 2-16 位的偶数长度纯十六进制字符串
                is_valid = (sid_str == "") or (re.match(r'^[0-9a-fA-F]+$', sid_str) and len(sid_str) % 2 == 0 and 2 <= len(sid_str) <= 16)
                
                if not is_valid:
                    # 格式异常，隔离到未测试列表，绝不进入 mihomo 测速队列
                    p['name'] = f"[未测试-格式异常] {name}"
                    untested_proxies.append(p)
                    untested_count += 1
                    continue # 跳过后续的正常入库逻辑

            # 3. 正常节点入库
            key = f"{name}|{server}|{port}|{ptype}"
            if key not in seen:
                seen.add(key)
                unique_proxies.append(p)

        print(f"  已排除 (中国/韩国) 节点: {excluded_count} 个")
        print(f"  已丢弃真正缺失字段的废节点: {invalid_count} 个")
        print(f"  已隔离格式异常的 REALITY 节点至 [未测试] 分类: {untested_count} 个")
        print(f"  触发 ip-api.com 检测次数: {ip_api_checked_count} 次 (已限速保护)")
        print(f"  待测速节点总数: {len(unique_proxies)}")

        if not unique_proxies:
            print("过滤后无有效节点，退出。")
            exit(1)

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

        print("\n步骤 3: 启动 mihomo 进行多线程真实协议测速...")
        temp_config = {
            'mixed-port': 7890, 'allow-lan': True, 'log-level': 'warning',
            'external-controller': '127.0.0.1:9090', 'proxies': alive_proxies,
            'proxy-groups': [{'name': 'TEST-GROUP', 'type': 'url-test', 'proxies': [p['name'] for p in alive_proxies], 'url': 'http://www.gstatic.com/generate_204', 'interval': 300}]
        }
        
        with open('temp.yaml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(temp_config, f, allow_unicode=True, sort_keys=False, width=1000)

        process = subprocess.Popen(['./mihomo', '-d', '.', '-f', 'temp.yaml'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5) 

        if process.poll() is not None:
            print(f"\nmihomo 进程意外退出！请检查 temp.yaml 格式。")
            exit(1)

        try:
            requests.get('http://127.0.0.1:9090/proxies/TEST-GROUP/delay?timeout=5000&url=http://www.gstatic.com/generate_204', timeout=60)
        except Exception:
            pass

        delay_map = {}
        try:
            res = requests.get('http://127.0.0.1:9090/proxies', timeout=10).json()
            test_group = res['proxies'].get('TEST-GROUP', {})
            for proxy in test_group.get('all', []):
                delay_map[proxy['name']] = proxy.get('history', [])[-1]['delay'] if proxy.get('history') else 99999
        except Exception:
            delay_map = {p['name']: 99999 for p in alive_proxies}

        process.terminate()
        process.wait()
        print("mihomo 核心已停止")

        # ==========================================
        # 步骤 4: 收集所有可用节点，并按国家分组排序
        # ==========================================
        print("\n步骤 4: 整理可用节点并生成双配置文件...")
        available_proxies = []
        available_country_groups = {}
        
        for p in alive_proxies:
            delay = delay_map.get(p['name'], 99999)
            if delay > 0 and delay < 5000:
                available_proxies.append(p)
                country = get_country_from_name(p['name']) or 'OTHER'
                if country not in available_country_groups:
                    available_country_groups[country] = []
                available_country_groups[country].append((p, delay))

        available_country_pools = {}
        for country, items in available_country_groups.items():
            items.sort(key=lambda x: x[1]) # 按延迟升序排序
            available_country_pools[f"{country}-POOL"] = [p['name'] for p, _ in items]
            print(f"  {country}: 共 {len(items)} 个可用节点 (最低延迟: {items[0][1]}ms)")

        print(f"\n总计筛选出 {len(available_proxies)} 个高质量可用节点")

        # ==========================================
        # 步骤 5: 生成 all-clash.yaml (包含所有可用节点 + 未测试节点)
        # ==========================================
        all_proxies_for_yaml = available_proxies + untested_proxies
        all_config = {
            'mixed-port': 7890, 'allow-lan': True, 'mode': 'rule', 'log-level': 'info',
            'ipv6': True, 'unified-delay': True, 'tcp-concurrent': True, 'global-client-fingerprint': 'chrome',
            'generated-by': 'github-actions-auto-merge-all', 
            'generated-at': datetime.now(timezone.utc).isoformat(),
            'proxies': all_proxies_for_yaml,
            'proxy-groups': build_proxy_groups(available_country_pools, available_proxies, untested_proxies, ai_pool_limit=100),
            'rules': ['DOMAIN-SUFFIX,openai.com,AI-POOL', 'DOMAIN-SUFFIX,chatgpt.com,AI-POOL', 'DOMAIN-SUFFIX,claude.ai,AI-POOL', 'DOMAIN-SUFFIX,anthropic.com,AI-POOL', 'GEOIP,CN,DIRECT', 'MATCH,PROXY']
        }

        with open('all-clash.yaml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(all_config, f, allow_unicode=True, sort_keys=False, width=1000)
        print("成功生成 all-clash.yaml (包含所有可用节点)")

        # ==========================================
        # 步骤 6: 生成 clash.yaml (每个国家仅保留 Top 20 + 未测试节点)
        # ==========================================
        top_20_proxies = []
        top_20_country_pools = {}
        for country, items in available_country_groups.items():
            top_20 = items[:20]
            for p, delay in top_20: 
                top_20_proxies.append(p)
            top_20_country_pools[f"{country}-POOL"] = [p['name'] for p, _ in top_20]

        top_20_proxies_for_yaml = top_20_proxies + untested_proxies
        top_20_config = {
            'mixed-port': 7890, 'allow-lan': True, 'mode': 'rule', 'log-level': 'info',
            'ipv6': True, 'unified-delay': True, 'tcp-concurrent': True, 'global-client-fingerprint': 'chrome',
            'generated-by': 'github-actions-auto-merge-top20', 
            'generated-at': datetime.now(timezone.utc).isoformat(),
            'proxies': top_20_proxies_for_yaml,
            'proxy-groups': build_proxy_groups(top_20_country_pools, top_20_proxies, untested_proxies, ai_pool_limit=50),
            'rules': ['DOMAIN-SUFFIX,openai.com,AI-POOL', 'DOMAIN-SUFFIX,chatgpt.com,AI-POOL', 'DOMAIN-SUFFIX,claude.ai,AI-POOL', 'DOMAIN-SUFFIX,anthropic.com,AI-POOL', 'GEOIP,CN,DIRECT', 'MATCH,PROXY']
        }

        with open('clash.yaml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(top_20_config, f, allow_unicode=True, sort_keys=False, width=1000)
        print("成功生成 clash.yaml (每个国家仅保留 Top 20)")

    except Exception as e:
        print("\n" + "="*50)
        print("脚本执行失败！详细错误信息如下：")
        traceback.print_exc()
        print("="*50)
        exit(1)

if __name__ == '__main__':
    main()
