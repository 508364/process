import yaml
import requests
import re
import subprocess
import time
import urllib.parse
import traceback
import os
import socket
import signal
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

BATCH_SIZE = 300
CONTROLLER_PORT = 9090

# 协议白名单:只保留 mihomo 明确支持的协议,其余直接丢弃
# 特别排除: anytls(mihomo 部分版本不支持)、wireguard(字段复杂易崩)
SUPPORTED_PROXY_TYPES = {
    'ss', 'ssr', 'vmess', 'vless', 'trojan', 'snell',
    'http', 'socks5',
    'hysteria', 'hysteria2', 'tuic',
}

# 每种协议必需字段
REQUIRED_FIELDS = {
    'vmess': ['uuid'],
    'vless': ['uuid'],
    'trojan': ['password'],
    'ss': ['cipher', 'password'],
    'ssr': ['cipher', 'password', 'protocol', 'obfs'],
    'snell': ['psk'],
    'hysteria': ['auth-str'],
    'hysteria2': ['password'],
    'tuic': [],
    'http': [],
    'socks5': [],
}

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

def fix_short_id(raw):
    """
    强制规范化 REALITY short-id:
    - 非空时必须为偶数长度纯 hex
    - 长度 0 或 2-16
    - 无法修复则返回空字符串 ""
    """
    if raw is None:
        return ""
    
    # int 类型:转成 hex 字符串(可能原始是 '0000' 被 YAML 吃掉前导零)
    if isinstance(raw, int):
        if raw == 0:
            return ""  # 0 大概率来自 '0000' 被吃零,直接置空最安全
        s = format(raw, 'x')
        if len(s) % 2 != 0:
            s = '0' + s
        if 2 <= len(s) <= 16:
            return s
        return ""
    
    s = str(raw).strip().strip('\'"')
    if s == "":
        return ""
    
    # 只保留 hex 字符
    clean = re.sub(r'[^0-9a-fA-F]', '', s)
    if clean == "":
        return ""
    
    if len(clean) % 2 != 0:
        clean = '0' + clean
    
    clean = clean[:16]
    
    if len(clean) < 2:
        return ""
    
    return clean.lower()

def normalize_proxy_types(p):
    """规范化字段类型"""
    ptype = str(p.get('type', '')).lower()
    p['type'] = ptype
    
    try:
        p['port'] = int(p['port'])
        if not (1 <= p['port'] <= 65535):
            return False
    except (ValueError, TypeError):
        return False
    
    server = str(p.get('server', '')).strip()
    if not server or ' ' in server:
        return False
    p['server'] = server
    
    if ptype == 'vmess':
        try:
            p['alterId'] = int(p.get('alterId', 0))
        except (ValueError, TypeError):
            p['alterId'] = 0
    
    return True

def validate_proxy_fields(p):
    ptype = str(p.get('type', '')).lower()
    if ptype not in SUPPORTED_PROXY_TYPES:
        return False
    required = REQUIRED_FIELDS.get(ptype, [])
    for field in required:
        val = p.get(field)
        if val is None or str(val).strip() == '':
            return False
    return True

def sanitize_reality_opts(p):
    """强制规范化 REALITY 相关字段,确保 mihomo 能识别"""
    ptype = str(p.get('type', '')).lower()
    if ptype != 'vless':
        return
    
    if 'reality-opts' not in p or not isinstance(p['reality-opts'], dict):
        return
    
    opts = p['reality-opts']
    
    # 强制 short-id 为合法字符串
    if 'short-id' in opts:
        opts['short-id'] = fix_short_id(opts['short-id'])
    else:
        # 如果没有 short-id,补一个空字符串
        opts['short-id'] = ""
    
    # public-key 也强制字符串化(虽然一般不会出问题)
    if 'public-key' in opts:
        opts['public-key'] = str(opts['public-key']).strip()

def wait_port_free(port, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', port))
            s.close()
            return True
        except OSError:
            time.sleep(0.5)
    return False

def kill_residual_mihomo():
    try:
        subprocess.run(
            ['pkill', '-9', '-f', './mihomo'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
    except Exception:
        pass
    time.sleep(1)

def _force_kill_process_group(process):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(1)

def write_mihomo_yaml(proxies, filename):
    """输出 mihomo 兼容的 YAML,short-id 强制加引号"""
    config = {
        'mixed-port': 7890,
        'allow-lan': True,
        'log-level': 'warning',
        'external-controller': f'127.0.0.1:{CONTROLLER_PORT}',
        'profile': {'store-selected': False, 'store-fake-ip': False},
        'proxies': proxies,
        'proxy-groups': [{
            'name': 'TEST-GROUP',
            'type': 'url-test',
            'proxies': [p['name'] for p in proxies],
            'url': 'http://www.gstatic.com/generate_204',
            'interval': 300
        }]
    }
    
    yaml_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=1000)
    
    # 强制 short-id 加双引号(防止 YAML 类型推断)
    yaml_text = re.sub(
        r'(short-id:\s*)([0-9a-fA-F]*)',
        lambda m: f'{m.group(1)}"{m.group(2)}"',
        yaml_text
    )
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(yaml_text)

def test_batch(proxies, batch_id, total_batches, allow_split=True):
    """
    单批次测速。若 mihomo 崩溃且 allow_split=True,自动二分拆批重试。
    返回 {name: delay}
    """
    if not proxies:
        return {}
    
    label = f"批次 {batch_id}/{total_batches}"
    print(f"  {label}: {len(proxies)} 个节点")
    
    kill_residual_mihomo()
    if not wait_port_free(CONTROLLER_PORT, timeout=30):
        print(f"    {label} 端口 {CONTROLLER_PORT} 30 秒未释放,跳过")
        return {}
    
    temp_file = f'temp_batch_{batch_id}.yaml'
    write_mihomo_yaml(proxies, temp_file)
    
    process = subprocess.Popen(
        ['./mihomo', '-d', '.', '-f', temp_file],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        preexec_fn=os.setsid
    )
    
    ready = False
    for _ in range(120):
        if process.poll() is not None:
            break
        try:
            r = requests.get(f'http://127.0.0.1:{CONTROLLER_PORT}/version', timeout=1)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    
    # mihomo 提前退出
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        print(f"    {label} mihomo 提前退出 (返回码: {process.returncode})")
        if stdout.strip():
            last_lines = stdout.strip().split('\n')[-3:]
            for line in last_lines:
                print(f"      {line[:220]}")
        
        try:
            os.rename(temp_file, f'failed_batch_{batch_id}.yaml')
        except Exception:
            pass
        _force_kill_process_group(process)
        
        # 二分拆批重试
        if allow_split and len(proxies) > 1:
            mid = len(proxies) // 2
            print(f"    {label} 二分拆批重试: {mid} + {len(proxies) - mid}")
            r1 = test_batch(proxies[:mid], f"{batch_id}a", total_batches, allow_split=False)
            r2 = test_batch(proxies[mid:], f"{batch_id}b", total_batches, allow_split=False)
            return {**r1, **r2}
        else:
            # 单个节点也失败,说明无法定位,丢弃
            return {}
    
    if not ready:
        print(f"    {label} mihomo 60 秒未就绪,强制终止")
        _force_kill_process_group(process)
        return {}
    
    # 触发测速
    try:
        requests.get(
            f'http://127.0.0.1:{CONTROLLER_PORT}/proxies/TEST-GROUP/delay'
            f'?timeout=5000&url=http://www.gstatic.com/generate_204',
            timeout=180
        )
    except Exception as e:
        print(f"    {label} 测速请求异常: {str(e)[:100]}")
    
    # 收集结果
    delay_map = {}
    try:
        res = requests.get(f'http://127.0.0.1:{CONTROLLER_PORT}/proxies', timeout=15).json()
        all_data = res.get('proxies', {})
        test_group = all_data.get('TEST-GROUP', {})
        for proxy_name in test_group.get('all', []):
            proxy_obj = all_data.get(proxy_name, {})
            history = proxy_obj.get('history', [])
            delay_map[proxy_name] = history[-1].get('delay', 99999) if history else 99999
        print(f"    {label} 成功获取 {len(delay_map)} 个测速结果")
    except Exception as e:
        print(f"    {label} 获取结果失败: {str(e)[:100]}")
        for p in proxies:
            delay_map[p['name']] = 99999
    
    _force_kill_process_group(process)
    
    try:
        if os.path.exists(temp_file):
            os.remove(temp_file)
    except Exception:
        pass
    
    if not wait_port_free(CONTROLLER_PORT, timeout=20):
        print(f"    {label} 结束后端口仍未释放")
    
    return delay_map

def build_proxy_groups(country_pools, active_proxies, untested_proxies, ai_pool_limit):
    pool_names = sorted(list(country_pools.keys()))
    groups = []
    
    groups.append({
        'name': 'AUTO-FAST', 'type': 'url-test',
        'proxies': [p['name'] for p in active_proxies],
        'url': 'http://www.gstatic.com/generate_204', 'interval': 120
    })
    
    for pool_name in pool_names:
        groups.append({
            'name': pool_name, 'type': 'url-test',
            'proxies': country_pools[pool_name],
            'url': 'http://www.gstatic.com/generate_204', 'interval': 120
        })
        
    if untested_proxies:
        groups.append({
            'name': 'UNTESTED', 'type': 'select',
            'proxies': [p['name'] for p in untested_proxies]
        })
        
    ai_pool_proxies = [p['name'] for p in active_proxies if re.search(r'\bUS\b|\bSG\b|\bCA\b|AI', p['name'], re.I)]
    if not ai_pool_proxies:
        ai_pool_proxies = [p['name'] for p in active_proxies]
        
    groups.append({
        'name': 'AI-POOL', 'type': 'url-test',
        'proxies': ai_pool_proxies[:ai_pool_limit],
        'url': 'http://www.gstatic.com/generate_204', 'interval': 120
    })
    
    fallback_proxies = ['AUTO-FAST'] + pool_names
    groups.append({
        'name': 'FALLBACK', 'type': 'fallback',
        'proxies': fallback_proxies,
        'url': 'http://www.gstatic.com/generate_204', 'interval': 120
    })
    
    proxy_proxies = ['AUTO-FAST', 'FALLBACK'] + pool_names
    if untested_proxies:
        proxy_proxies.append('UNTESTED')
        
    groups.append({
        'name': 'PROXY', 'type': 'select',
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
            print("\n未找到任何有效节点,退出。")
            exit(1)

        print(f"\n合并后节点总数: {len(all_proxies)}")

        print("\n步骤 2: 执行节点清洗、去重、类型规范化与国家识别...")
        seen_keys = set()
        seen_names = set()
        unique_proxies = []
        untested_proxies = []
        excluded_count = 0
        invalid_count = 0
        invalid_field_count = 0
        type_error_count = 0
        unsupported_type_count = 0
        renamed_count = 0
        ip_api_checked_count = 0
        untested_count = 0
        
        for p in all_proxies:
            if not isinstance(p, dict): 
                invalid_count += 1
                continue
            
            if not all(k in p and p[k] for k in ['name', 'server', 'port', 'type']):
                invalid_count += 1
                continue
            
            if not normalize_proxy_types(p):
                type_error_count += 1
                continue
            
            # 协议白名单
            if not validate_proxy_fields(p):
                ptype = str(p.get('type', '')).lower()
                if ptype not in SUPPORTED_PROXY_TYPES:
                    unsupported_type_count += 1
                else:
                    invalid_field_count += 1
                continue
            
            # 规范化 REALITY short-id
            sanitize_reality_opts(p)
                
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
            
            dedup_key = f"{server}|{port}|{ptype}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            
            base = name
            final = base
            counter = 1
            while final in seen_names:
                counter += 1
                final = f"{base}-{counter}"
            if final != base:
                renamed_count += 1
            seen_names.add(final)
            p['name'] = final
            
            unique_proxies.append(p)

        print(f"  已排除 (中国/韩国) 节点: {excluded_count} 个")
        print(f"  已丢弃真正缺失字段的废节点: {invalid_count} 个")
        print(f"  已丢弃协议必需字段缺失的节点: {invalid_field_count} 个")
        print(f"  已丢弃 mihomo 不支持的协议: {unsupported_type_count} 个")
        print(f"  已丢弃字段类型错误的节点: {type_error_count} 个")
        print(f"  已重命名以避免重名的节点: {renamed_count} 个")
        print(f"  已隔离格式异常的 REALITY 节点至 UNTESTED 分类: {untested_count} 个")
        print(f"  待测速节点总数: {len(unique_proxies)}")

        if not unique_proxies:
            print("过滤后无有效节点,退出。")
            exit(1)

        print("\n步骤 2.5: 启动智能初筛 (UDP协议直接放行,TCP协议极速Ping)...")
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

        print(f"  初筛完成!剔除死节点 {len(unique_proxies) - len(alive_proxies)} 个,剩余 {len(alive_proxies)} 个节点进入 mihomo 真实测速。")

        # ==========================================
        # 步骤 3: 分批进行 mihomo 真实协议测速
        # ==========================================
        total_batches = (len(alive_proxies) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n步骤 3: 分批启动 mihomo 进行真实协议测速 (共 {total_batches} 批,每批最多 {BATCH_SIZE} 个)...")
        
        kill_residual_mihomo()
        wait_port_free(CONTROLLER_PORT, timeout=20)
        
        delay_map = {}
        for batch_idx in range(total_batches):
            start = batch_idx * BATCH_SIZE
            end = min(start + BATCH_SIZE, len(alive_proxies))
            batch = alive_proxies[start:end]
            
            batch_delays = test_batch(batch, batch_idx + 1, total_batches)
            delay_map.update(batch_delays)

        valid_delays = [d for d in delay_map.values() if 0 < d < 5000]
        print(f"\n  测速完成: 有效延迟数据 {len(valid_delays)} 个")

        # ==========================================
        # 步骤 4: 收集所有可用节点,并按国家分组排序
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
            items.sort(key=lambda x: x[1])
            available_country_pools[f"{country}-POOL"] = [p['name'] for p, _ in items]
            print(f"  {country}: 共 {len(items)} 个可用节点 (最低延迟: {items[0][1]}ms)")

        print(f"\n总计筛选出 {len(available_proxies)} 个高质量可用节点")

        if not available_proxies:
            print("\n未测出任何可用节点,不生成输出文件。")
            exit(1)

        # ==========================================
        # 步骤 5: 生成 all-clash.yaml
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

        # 最终文件也强制 short-id 加引号
        yaml_text = yaml.safe_dump(all_config, allow_unicode=True, sort_keys=False, width=1000)
        yaml_text = re.sub(
            r'(short-id:\s*)([0-9a-fA-F]*)',
            lambda m: f'{m.group(1)}"{m.group(2)}"',
            yaml_text
        )
        with open('all-clash.yaml', 'w', encoding='utf-8') as f:
            f.write(yaml_text)
        print("成功生成 all-clash.yaml (包含所有可用节点)")

        # ==========================================
        # 步骤 6: 生成 clash.yaml (每个国家仅保留 Top 20)
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

        yaml_text = yaml.safe_dump(top_20_config, allow_unicode=True, sort_keys=False, width=1000)
        yaml_text = re.sub(
            r'(short-id:\s*)([0-9a-fA-F]*)',
            lambda m: f'{m.group(1)}"{m.group(2)}"',
            yaml_text
        )
        with open('clash.yaml', 'w', encoding='utf-8') as f:
            f.write(yaml_text)
        print("成功生成 clash.yaml (每个国家仅保留 Top 20)")

    except Exception as e:
        print("\n" + "="*50)
        print("脚本执行失败!详细错误信息如下:")
        traceback.print_exc()
        print("="*50)
        exit(1)

if __name__ == '__main__':
    main()
