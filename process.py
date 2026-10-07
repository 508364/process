import sys
# ============== 关键修复:强制无缓冲输出,防止崩溃时日志丢失 ==============
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
# =====================================================================

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
    "https://raw.githubusercontent.com/ninjastrikers/Nexus-nodes/main/configs/all.txt",
    "https://thordata.github.io/awesome-free-proxy-list/data/clash/all.yaml",
    "https://raw.githubusercontent.com/linzjian666/chromego_extractor/main/outputs/clash_meta.yaml",
    "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub",
    "https://raw.githubusercontent.com/Leo-Leejianzhao/RSS/refs/heads/main/subscribe/clash.yml"
]

IP_API_CALLS = 0
MAX_IP_API_CALLS = 40
UDP_ONLY_PROTOCOLS = {'hysteria', 'hysteria2', 'tuic', 'snell'}

BATCH_SIZE = 100
CONTROLLER_PORT = 9090
HEALTH_CHECK_URL = [ 
    'http://cp.cloudflare.com/generate_204',
    'http://www.gstatic.com/generate_204',
    'http://www.qualcomm.cn/generate_204' ]

HEALTH_CHECK_TIMEOUT = 8000
DELAY_THRESHOLD = 8000

SUPPORTED_PROXY_TYPES = {
    'ss', 'ssr', 'vmess', 'vless', 'trojan', 'snell',
    'hysteria', 'hysteria2', 'tuic',
}

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

class QuotedStr(str):
    pass

def _quoted_str_representer(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', str(data), style='"')

yaml.SafeDumper.add_representer(QuotedStr, _quoted_str_representer)
yaml.SafeDumper.add_representer(str, yaml.SafeDumper.yaml_representers[str])

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
    if raw is None:
        return QuotedStr("")
    if isinstance(raw, int):
        if raw == 0:
            return QuotedStr("")
        s = format(raw, 'x')
        if len(s) % 2 != 0:
            s = '0' + s
        if 2 <= len(s) <= 16:
            return QuotedStr(s.lower())
        return QuotedStr("")
    s = str(raw).strip().strip('\'"')
    if s == "":
        return QuotedStr("")
    clean = re.sub(r'[^0-9a-fA-F]', '', s)
    if clean == "":
        return QuotedStr("")
    if len(clean) % 2 != 0:
        clean = '0' + clean
    clean = clean[:16]
    if len(clean) < 2:
        return QuotedStr("")
    return QuotedStr(clean.lower())

def normalize_proxy_types(p):
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
    ptype = str(p.get('type', '')).lower()
    if ptype != 'vless':
        return
    if 'reality-opts' not in p or not isinstance(p['reality-opts'], dict):
        return
    opts = p['reality-opts']
    if 'short-id' in opts:
        opts['short-id'] = fix_short_id(opts['short-id'])
    else:
        opts['short-id'] = QuotedStr("")
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

def pick_health_check_url():
    """多测速URL降级：选第一个可用的"""
    for url in HEALTH_CHECK_URLS:
        try:
            r = requests.head(url, timeout=5)
            if r.status_code in (200, 204):
                return url
        except Exception:
            continue
    return HEALTH_CHECK_URLS[0]

def write_mihomo_yaml(proxies, filename, health_url):
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
            'url': health_url,
            'interval': 300
        }]
    }
    with open(filename, 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, width=1000)

def test_batch(proxies, batch_id, total_batches, health_url, allow_split=True):
    if not proxies:
        return {}
    
    label = f"批次 {batch_id}/{total_batches}"
    print(f"  {label}: {len(proxies)} 个节点")
    
    kill_residual_mihomo()
    if not wait_port_free(CONTROLLER_PORT, timeout=30):
        print(f"    {label} 端口 {CONTROLLER_PORT} 30 秒未释放,跳过")
        return {}
    
    temp_file = f'temp_batch_{batch_id}.yaml'
    log_file = f'mihomo_batch_{batch_id}.log'
    write_mihomo_yaml(proxies, temp_file, health_url)
    
    # ============== 关键修复:输出到文件而非 PIPE,避免管道缓冲死锁 ==============
    with open(log_file, 'w') as logf:
        process = subprocess.Popen(
            ['./mihomo', '-d', '.', '-f', temp_file],
            stdout=logf, stderr=subprocess.STDOUT,
            preexec_fn=os.setsid
        )
    # =====================================================================
    
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
    
    if process.poll() is not None:
        print(f"    {label} mihomo 提前退出 (返回码: {process.returncode})")
        # 从日志文件读取最后几行
        try:
            with open(log_file, 'r') as lf:
                lines = lf.readlines()
            for line in lines[-5:]:
                print(f"      {line.rstrip()[:220]}")
        except Exception:
            pass
        try:
            os.rename(temp_file, f'failed_batch_{batch_id}.yaml')
        except Exception:
            pass
        _force_kill_process_group(process)
        if allow_split and len(proxies) > 1:
            mid = len(proxies) // 2
            print(f"    {label} 二分拆批重试: {mid} + {len(proxies) - mid}")
            r1 = test_batch(proxies[:mid], f"{batch_id}a", total_batches, health_url, allow_split=False)
            r2 = test_batch(proxies[mid:], f"{batch_id}b", total_batches, health_url, allow_split=False)
            return {**r1, **r2}
        else:
            return {}
    
    if not ready:
        print(f"    {label} mihomo 60 秒未就绪,强制终止")
        _force_kill_process_group(process)
        return {}
    
    try:
        requests.get(
            f'http://127.0.0.1:{CONTROLLER_PORT}/proxies/TEST-GROUP/delay'
            f'?timeout={HEALTH_CHECK_TIMEOUT}&url={health_url}',
            timeout=60
        )
    except Exception as e:
        print(f"    {label} 触发测速异常: {str(e)[:100]}")
    
    # 轮询等待95%节点产生history
    start_wait = time.time()
    target_count = max(1, int(len(proxies) * 0.95))
    print(f"    {label} 等待测速完成 (目标 {target_count}/{len(proxies)})...")
    while time.time() - start_wait < 400:
        try:
            r = requests.get(f'http://127.0.0.1:{CONTROLLER_PORT}/proxies', timeout=10).json()
            all_data = r.get('proxies', {})
            tg = all_data.get('TEST-GROUP', {})
            done = sum(1 for n in tg.get('all', []) 
                       if all_data.get(n, {}).get('history'))
            if done >= target_count:
                break
        except Exception:
            pass
        time.sleep(3)
    
    delay_map = {}
    stat = {'有效': 0, '失败': 0, '超时': 0, '无数据': 0}
    try:
        res = requests.get(f'http://127.0.0.1:{CONTROLLER_PORT}/proxies', timeout=15).json()
        all_data = res.get('proxies', {})
        test_group = all_data.get('TEST-GROUP', {})
        for proxy_name in test_group.get('all', []):
            proxy_obj = all_data.get(proxy_name, {})
            history = proxy_obj.get('history', [])
            if not history:
                delay_map[proxy_name] = 99999
                stat['无数据'] += 1
            else:
                d = history[-1].get('delay', 99999)
                delay_map[proxy_name] = d
                if d == 0:
                    stat['失败'] += 1
                elif d >= HEALTH_CHECK_TIMEOUT:
                    stat['超时'] += 1
                elif d < DELAY_THRESHOLD:
                    stat['有效'] += 1
                else:
                    stat['无数据'] += 1
        print(f"    {label} 测速统计: 有效={stat['有效']} 失败={stat['失败']} 超时={stat['超时']} 无数据={stat['无数据']}")
    except Exception as e:
        print(f"    {label} 获取结果失败: {str(e)[:100]}")
        for p in proxies:
            delay_map[p['name']] = 99999
    
    _force_kill_process_group(process)
    
    try:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        if os.path.exists(log_file):
            os.remove(log_file)
    except Exception:
        pass
    
    if not wait_port_free(CONTROLLER_PORT, timeout=20):
        print(f"    {label} 结束后端口仍未释放")
    
    return delay_map

def build_proxy_groups(country_pools, active_proxies, untested_proxies, ai_pool_limit, health_url):
    pool_names = sorted(list(country_pools.keys()))
    groups = []
    groups.append({
        'name': 'AUTO-FAST', 'type': 'url-test',
        'proxies': [p['name'] for p in active_proxies],
        'url': health_url, 'interval': 120
    })
    for pool_name in pool_names:
        groups.append({
            'name': pool_name, 'type': 'url-test',
            'proxies': country_pools[pool_name],
            'url': health_url, 'interval': 120
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
        'url': health_url, 'interval': 120
    })
    fallback_proxies = ['AUTO-FAST'] + pool_names
    groups.append({
        'name': 'FALLBACK', 'type': 'fallback',
        'proxies': fallback_proxies,
        'url': health_url, 'interval': 120
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
            sys.exit(1)

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
            if not validate_proxy_fields(p):
                ptype = str(p.get('type', '')).lower()
                if ptype not in SUPPORTED_PROXY_TYPES:
                    unsupported_type_count += 1
                else:
                    invalid_field_count += 1
                continue
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
        print(f"  待测速节点总数: {len(unique_proxies)}")

        if not unique_proxies:
            print("过滤后无有效节点,退出。")
            sys.exit(1)

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

        total_batches = (len(alive_proxies) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n步骤 3: 分批启动 mihomo 进行真实协议测速 (共 {total_batches} 批,每批最多 {BATCH_SIZE} 个)...")
        print(f"  测速 URL: {HEALTH_CHECK_URL}")
        print(f"  单节点超时: {HEALTH_CHECK_TIMEOUT}ms")
        
        kill_residual_mihomo()
        wait_port_free(CONTROLLER_PORT, timeout=20)
        
        health_url = pick_health_check_url()
        print(f"  选用测速 URL: {health_url}")
        
        delay_map = {}
        for batch_idx in range(total_batches):
            start = batch_idx * BATCH_SIZE
            end = min(start + BATCH_SIZE, len(alive_proxies))
            batch = alive_proxies[start:end]
            batch_delays = test_batch(batch, batch_idx + 1, total_batches, health_url)
            delay_map.update(batch_delays)

        valid_delays = [d for d in delay_map.values() if 0 < d < DELAY_THRESHOLD]
        print(f"\n  测速完成: 有效延迟数据 {len(valid_delays)} 个 / 总节点 {len(alive_proxies)} 个 (存活率 {100*len(valid_delays)//max(1,len(alive_proxies))}%)")

        print("\n步骤 4: 整理可用节点并生成双配置文件...")
        available_proxies = []
        available_country_groups = {}
        
        for p in alive_proxies:
            delay = delay_map.get(p['name'], 99999)
            if 0 < delay < DELAY_THRESHOLD:
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
            sys.exit(1)

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
        print("脚本执行失败!详细错误信息如下:")
        traceback.print_exc()
        print("="*50)
        sys.exit(1)

if __name__ == '__main__':
    main()
