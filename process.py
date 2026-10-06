import yaml
import requests
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone

# 1. 配置需要整合的订阅链接
URLS = [
    "https://raw.githubusercontent.com/dongchengjie/airport/main/subs/merged/tested_within.yaml",
    "https://sunmiao4458.github.io/free-proxy-airport/clash.yaml",
    "https://raw.githubusercontent.com/Ruk1ng001/freeSub/main/clash.yaml",
    "https://raw.githubusercontent.com/a2470982985/getNode/main/clash.yaml",
    "https://raw.githubusercontent.com/SnapdragonLee/SystemProxy/master/dist/clash_config.yaml",
    "https://raw.githubusercontent.com/ninjastrikers/Nexus-nodes/main/configs/all.txt"
]

# 全局计数器：保护 ip-api.com 免受限速封禁 (免费版限制 45次/分钟)
IP_API_CALLS = 0
MAX_IP_API_CALLS = 40  # 保守设置为 40，留出安全余量

def clean_name(name):
    """清洗节点名称：移除所有 Emoji 和特殊符号，减小 YAML 体积"""
    if not name:
        return "Unknown_Node"
    # 仅保留：字母、数字、中文、基本标点符号 (- _ . / [ ] ( ) )
    name = re.sub(r'[^\w\s\u4e00-\u9fa5\-_\.\[\]\(\)\/]', '', str(name))
    return name.strip()[:50]

def is_excluded_by_name(name):
    """第一道防线：通过名称直接排除中国大陆和韩国"""
    name_upper = name.upper()
    exclude_keywords = ['中国', '大陆', 'CN', 'CHN', 'MAINLAND', '韩国', 'KR', 'KOR', 'KOREA']
    return any(keyword in name_upper or keyword in name for keyword in exclude_keywords)

def get_country_from_name(name):
    """从纯文本名称中提取国家代码 (无 Emoji)"""
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
    return None # 未知

def get_country_via_ip_api(server):
    """第二道防线：仅对未知节点，通过 ip-api.com 查询真实地理位置"""
    global IP_API_CALLS
    
    # 达到安全上限，停止查询，避免被 ban
    if IP_API_CALLS >= MAX_IP_API_CALLS:
        return 'OTHER'
    
    try:
        IP_API_CALLS += 1
        # 仅请求 countryCode 和 status，减小响应体积
        url = f"http://ip-api.com/json/{server}?fields=countryCode,status"
        resp = requests.get(url, timeout=4)
        data = resp.json()
        
        # 严格限速：每次请求后强制休眠 1.5 秒 (60秒 / 1.5秒 = 40次/分钟)
        time.sleep(1.5) 
        
        if data.get('status') == 'success':
            country_code = data.get('countryCode', 'OTHER').upper()
            # 第二道防线：如果 ip-api 查出是中国或韩国，同样排除
            if country_code in ['CN', 'KR']:
                return 'EXCLUDED'
            return country_code
    except Exception:
        # 即使请求失败，也要休眠，防止连续快速重试触发封禁
        time.sleep(1.5)
    
    return 'OTHER'

def main():
    print("步骤 1: 启动 subconverter 统一转换所有订阅格式...")
    sub_process = subprocess.Popen(['./subconverter', '-d'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)

    try:
        combined_url = '|'.join(URLS)
        encoded_url = urllib.parse.quote(combined_url)
        api_url = f"http://127.0.0.1:25500/sub?target=clash&url={encoded_url}&insert=false&emoji=false&sort=false&scv=true"
        
        print("  正在请求转换接口 (已禁用 emoji 输出)...")
        resp = requests.get(api_url, timeout=60)
        resp.raise_for_status()
        
        unified_data = yaml.safe_load(resp.text)
        all_proxies = unified_data.get('proxies', [])
        print(f"  subconverter 成功提取 {len(all_proxies)} 个节点")
    except Exception as e:
        print(f"  subconverter 转换失败: {e}")
        all_proxies = []
    finally:
        sub_process.terminate()
        sub_process.wait()

    if not all_proxies:
        print("未找到任何有效节点，退出。")
        exit(1)

    # 2. 过滤 & 去重 & 国家识别
    seen = set()
    unique_proxies = []
    excluded_count = 0
    ip_api_checked_count = 0
    
    print("\n步骤 2: 执行节点清洗、去重与国家识别 (含 ip-api 降级检测)...")
    for p in all_proxies:
        if not isinstance(p, dict): continue
        
        original_name = p.get('name', '')
        name = clean_name(original_name)
        p['name'] = name
        
        # 第一道防线：名称包含中韩特征，直接丢弃
        if is_excluded_by_name(name):
            excluded_count += 1
            continue
            
        country = get_country_from_name(name)
        
        # 如果名称无法识别，触发 ip-api 降级检测
        if country is None:
            ip_api_checked_count += 1
            country = get_country_via_ip_api(p.get('server', ''))
            
        # 第二道防线：ip-api 查出是中韩，丢弃
        if country == 'EXCLUDED':
            excluded_count += 1
            continue
            
        # 兜底：如果依然未知，标记为 OTHER
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

    # 3. 生成用于 mihomo 测速的临时配置
    temp_config = {
        'mixed-port': 7890,
        'allow-lan': True,
        'log-level': 'warning',
        'external-controller': '127.0.0.1:9090',
        'proxies': unique_proxies,
        'proxy-groups': [{
            'name': 'TEST-GROUP',
            'type': 'url-test',
            'proxies': [p['name'] for p in unique_proxies],
            'url': 'http://www.gstatic.com/generate_204',
            'interval': 300
        }]
    }
    with open('temp.yaml', 'w', encoding='utf-8') as f:
        yaml.safe_dump(temp_config, f, allow_unicode=True, sort_keys=False)

    # 4. 启动 mihomo 进行 Go 协程多线程真实协议测速
    print("\n步骤 3: 启动 mihomo 进行多线程并发测速 (支持全协议)...")
    process = subprocess.Popen(['./mihomo', '-d', '.', '-f', 'temp.yaml'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6) 

    try:
        print("  正在发送多线程测速指令 (Timeout: 5000ms)...")
        requests.get('http://127.0.0.1:9090/proxies/TEST-GROUP/delay?timeout=5000&url=http://www.gstatic.com/generate_204', timeout=120)
    except Exception as e:
        print(f"  测速请求异常: {e}")

    # 5. 获取测速结果
    delay_map = {}
    try:
        res = requests.get('http://127.0.0.1:9090/proxies', timeout=10).json()
        test_group = res['proxies'].get('TEST-GROUP', {})
        for proxy in test_group.get('all', []):
            name = proxy['name']
            history = proxy.get('history', [])
            delay = history[-1]['delay'] if history else 99999
            delay_map[name] = delay
    except Exception as e:
        print(f"  获取测速结果失败: {e}")
        delay_map = {p['name']: 99999 for p in unique_proxies}

    process.terminate()
    process.wait()
    print("mihomo 核心已停止")

    # 6. 按国家分组，取每个国家前 20 个
    country_groups = {}
    for p in unique_proxies:
        name = p['name']
        delay = delay_map.get(name, 99999)
        
        # 过滤掉测试失败 (delay=0) 或超时 (delay>=5000) 的节点
        if delay == 0 or delay >= 5000:
            continue
        
        # 重新获取该节点的国家 (因为之前可能存在动态分配)
        country = get_country_from_name(name) or 'OTHER'
        if country not in country_groups:
            country_groups[country] = []
        country_groups[country].append((p, delay))

    final_proxies = []
    country_pools = {}
    
    print("\n步骤 4: 按国家筛选 Top 20 低延迟节点:")
    for country, items in country_groups.items():
        items.sort(key=lambda x: x[1])  # 按延迟升序排序
        top_20 = items[:20]             # 只取前 20 个
        for p, delay in top_20:
            final_proxies.append(p)
        
        pool_name = f"{country}-POOL"
        country_pools[pool_name] = [p['name'] for p, _ in top_20]
        print(f"  {country}: 保留 {len(top_20)} 个 (最低延迟: {top_20[0][1]}ms)")

    print(f"\n最终筛选出 {len(final_proxies)} 个高质量节点")

    # 7. 构建最终目标样式的 clash.yaml (纯文本，无 Emoji)
    pool_names = sorted(list(country_pools.keys()))
    
    # AI 节点池：优先选择 US, SG, CA 的节点
    ai_pool_proxies = [p['name'] for p in final_proxies if re.search(r'\bUS\b|\bSG\b|\bCA\b|AI', p['name'], re.I)]
    if not ai_pool_proxies:
        ai_pool_proxies = [p['name'] for p in final_proxies]

    final_config = {
        'mixed-port': 7890,
        'allow-lan': True,
        'mode': 'rule',
        'log-level': 'info',
        'ipv6': True,
        'unified-delay': True,
        'tcp-concurrent': True,
        'global-client-fingerprint': 'chrome',
        'generated-by': 'github-actions-auto-merge-v4',
        'generated-at': datetime.now(timezone.utc).isoformat(),
        'proxies': final_proxies,
        'proxy-groups': [
            {
                'name': 'AUTO-FAST',
                'type': 'url-test',
                'proxies': [p['name'] for p in final_proxies],
                'url': 'http://www.gstatic.com/generate_204',
                'interval': 120
            }
        ],
        'rules': [
            'DOMAIN-SUFFIX,openai.com,AI-POOL',
            'DOMAIN-SUFFIX,chatgpt.com,AI-POOL',
            'DOMAIN-SUFFIX,claude.ai,AI-POOL',
            'DOMAIN-SUFFIX,anthropic.com,AI-POOL',
            'GEOIP,CN,DIRECT',
            'MATCH,PROXY'
        ]
    }

    # 动态添加各个国家的 POOL
    for pool_name in pool_names:
        final_config['proxy-groups'].append({
            'name': pool_name,
            'type': 'url-test',
            'proxies': country_pools[pool_name],
            'url': 'http://www.gstatic.com/generate_204',
            'interval': 120
        })

    # 添加 AI-POOL, FALLBACK, PROXY
    final_config['proxy-groups'].extend([
        {
            'name': 'AI-POOL',
            'type': 'url-test',
            'proxies': ai_pool_proxies[:50],
            'url': 'http://www.gstatic.com/generate_204',
            'interval': 120
        },
        {
            'name': 'FALLBACK',
            'type': 'fallback',
            'proxies': ['AUTO-FAST'] + pool_names,
            'url': 'http://www.gstatic.com/generate_204',
            'interval': 120
        },
        {
            'name': 'PROXY',
            'type': 'select',
            'proxies': ['AUTO-FAST', 'FALLBACK'] + pool_names
        }
    ])

    with open('clash.yaml', 'w', encoding='utf-8') as f:
        yaml.safe_dump(final_config, f, allow_unicode=True, sort_keys=False, width=1000)

    print("成功生成 clash.yaml，等待 Git 提交...")

if __name__ == '__main__':
    main()