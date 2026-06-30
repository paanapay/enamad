<?php
/**
 * استخراج فهرست دارندگان اینماد از enamad.ir/DomainListForMIMT
 *
 * مرورگر:  http://localhost/enamad/
 * CLI:     php index.php [--output=FILE] [--format=csv|json] [--limit=N] [--captcha=CODE]
 */

declare(strict_types=1);

const BASE_URL = 'https://enamad.ir/';
const PAGE_SIZE = 30;
const COOKIE_FILE = __DIR__ . '/enamad_cookies.txt';

if (PHP_SAPI !== 'cli') {
  runWeb();
  exit;
}

runCli();

function runCli(): void
{
  $options = getopt('', [
    'output:',
    'format::',
    'page::',
    'limit::',
    'delay::',
    'captcha::',
    'help',
  ]);

  if (isset($options['help'])) {
    echo <<<HELP
استخراج فهرست دارندگان اینماد

گزینه‌ها:
  --output=FILE     مسیر فایل خروجی (پیش‌فرض: enamad_domains.csv)
  --format=csv|json فرمت خروجی (پیش‌فرض: csv)
  --page=N          شروع از صفحه N (پیش‌فرض: 1)
  --limit=N         حداکثر تعداد صفحه (پیش‌فرض: 1)
  --delay=SEC       تأخیر بین درخواست‌ها (پیش‌فرض: 1)
  --captcha=CODE    کد کپچا
  --help            نمایش راهنما

HELP;
    exit(0);
  }

  $config = parseConfig($options, false);
  $client = new EnamadClient(COOKIE_FILE);

  echo "در حال دریافت توکن...\n";
  $captchaData = $client->refreshCaptcha();

  $result = extractDomains(
    $client,
    $config,
    $captchaData,
    $config['captcha'],
    function (string $message) use ($client, &$captchaData, &$config): string {
      echo "{$message}\n";
      return promptCaptchaCli($client, $captchaData, $config['captcha']);
    }
  );

  saveOutput($result['rows'], $config['output'], $config['format']);
  echo "انجام شد. " . count($result['rows']) . " رکورد در {$config['output']} ذخیره شد.\n";
}

function runWeb(): void
{
  $action = $_GET['action'] ?? '';

  if ($action === 'captcha') {
    handleCaptchaAction();
    return;
  }

  if ($action === 'fetch') {
    handleFetchAction();
    return;
  }

  if ($action === 'save') {
    handleSaveAction();
    return;
  }

  renderWebPage();
}

function handleCaptchaAction(): void
{
  header('Content-Type: application/json; charset=UTF-8');

  try {
    $client = new EnamadClient(COOKIE_FILE);
    $data = $client->refreshCaptcha();
    echo json_encode([
      'ok' => true,
      'captha' => $data['captha'] ?? $data['captcha'] ?? '',
      'cptToken' => $data['cptToken'],
    ], JSON_UNESCAPED_UNICODE);
  } catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
  }
}

function handleFetchAction(): void
{
  header('Content-Type: application/json; charset=UTF-8');
  set_time_limit(120);

  try {
    $input = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($input)) {
      throw new RuntimeException('درخواست نامعتبر.');
    }

    $page = max(1, (int) ($input['page'] ?? 1));
    $token = trim((string) ($input['cpt_token'] ?? ''));
    $captchaCode = trim((string) ($input['captcha'] ?? ''));
    $verified = !empty($input['verified']);

    $client = new EnamadClient(COOKIE_FILE);

    if ($page === 1) {
      if ($token === '' || $captchaCode === '') {
        throw new RuntimeException('کد امنیتی و توکن الزامی است.');
      }

      $captchaData = ['cptToken' => $token];
      $useBypass = false;
    } else {
      if (!$verified) {
        throw new RuntimeException('ابتدا صفحه اول باید با کپچا تأیید شود.');
      }

      $captchaData = $client->refreshCaptcha();
      $token = $captchaData['cptToken'];
      $captchaCode = '';
      $useBypass = false;
    }

    $response = $client->getDomainList(
      page: $page,
      token: $token,
      checkCaptchaBypass: $useBypass,
      captchaCode: $captchaCode
    );

    if ((int) ($response['result'] ?? 0) !== 1) {
      $message = $response['result_msg'] ?? 'خطای نامشخص';
      throw new RuntimeException($message);
    }

    $domains = $response['applicantDomainsList'] ?? [];
    $totalPages = max(1, (int) ($response['page'] ?? 1));
    $rows = [];

    foreach ($domains as $index => $item) {
      $rowNumber = (($page - 1) * PAGE_SIZE) + $index + 1;
      $rows[] = normalizeDomainRow($item, $rowNumber);
    }

    echo json_encode([
      'ok' => true,
      'page' => $page,
      'totalPages' => $totalPages,
      'count' => count($rows),
      'rows' => $rows,
      'verified' => true,
    ], JSON_UNESCAPED_UNICODE);
  } catch (Throwable $e) {
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
  }
}

function handleSaveAction(): void
{
  header('Content-Type: application/json; charset=UTF-8');

  try {
    $input = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($input) || empty($input['rows']) || !is_array($input['rows'])) {
      throw new RuntimeException('داده‌ای برای ذخیره وجود ندارد.');
    }

    $config = parseConfig([
      'output' => $input['output'] ?? 'enamad_domains.csv',
      'format' => $input['format'] ?? 'csv',
    ], true);

    saveOutput($input['rows'], $config['output'], $config['format']);

    echo json_encode([
      'ok' => true,
      'file' => basename($config['output']),
      'count' => count($input['rows']),
    ], JSON_UNESCAPED_UNICODE);
  } catch (Throwable $e) {
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => $e->getMessage()], JSON_UNESCAPED_UNICODE);
  }
}

/** @param array<string, mixed> $input */
function parseConfig(array $input, bool $isWeb): array
{
  $output = $input['output'] ?? (__DIR__ . '/enamad_domains.csv');
  if (!str_contains((string) $output, DIRECTORY_SEPARATOR) && !str_contains((string) $output, '/')) {
    $output = __DIR__ . DIRECTORY_SEPARATOR . $output;
  }

  $format = strtolower((string) ($input['format'] ?? (str_ends_with((string) $output, '.json') ? 'json' : 'csv')));

  if (!in_array($format, ['csv', 'json'], true)) {
    throw new RuntimeException('فرمت نامعتبر. فقط csv یا json.');
  }

  $defaultLimit = $isWeb ? 1 : 1;
  $pageLimit = isset($input['limit']) && $input['limit'] !== ''
    ? max(1, (int) $input['limit'])
    : $defaultLimit;

  return [
    'output' => (string) $output,
    'format' => $format,
    'start_page' => max(1, (int) ($input['page'] ?? 1)),
    'page_limit' => $pageLimit,
    'delay' => max(0, (float) ($input['delay'] ?? 1)),
    'captcha' => isset($input['captcha']) && $input['captcha'] !== '' ? trim((string) $input['captcha']) : null,
  ];
}

/**
 * @param array{cptToken: string, captha?: string} $captchaData
 * @param callable(string): string $onCaptchaRequired
 * @return array{rows: list<array<string, mixed>>}
 */
function extractDomains(
  EnamadClient $client,
  array $config,
  array $captchaData,
  ?string $initialCaptcha,
  callable $onCaptchaRequired
): array {
  $allDomains = [];
  $currentPage = $config['start_page'];
  $totalPages = null;
  $pagesFetched = 0;
  $captchaVerified = false;
  $useCaptchaBypass = false;
  $captchaCode = $initialCaptcha ?? '';

  while (true) {
    if ($pagesFetched >= $config['page_limit']) {
      break;
    }

    if ($totalPages !== null && $currentPage > $totalPages) {
      break;
    }

    if ($captchaVerified) {
      $captchaData = $client->refreshCaptcha();
      $captchaCode = '';
    }

    $response = $client->getDomainList(
      page: $currentPage,
      token: $captchaData['cptToken'],
      checkCaptchaBypass: $useCaptchaBypass,
      captchaCode: $captchaCode
    );

    if ((int) ($response['result'] ?? 0) !== 1) {
      $message = $response['result_msg'] ?? 'خطای نامشخص';

      if (!$captchaVerified && $captchaCode === '' && needsCaptcha($message)) {
        $captchaCode = $onCaptchaRequired('نیاز به کپچا: ' . $message);
        continue;
      }

      if (needsCaptcha($message)) {
        throw new RuntimeException('کد امنیتی اشتباه است: ' . $message);
      }

      throw new RuntimeException("خطا در صفحه {$currentPage}: {$message}");
    }

    $domains = $response['applicantDomainsList'] ?? [];
    $totalPages = max(1, (int) ($response['page'] ?? 1));

    if ($domains === []) {
      break;
    }

    foreach ($domains as $index => $item) {
      $rowNumber = (($currentPage - 1) * PAGE_SIZE) + $index + 1;
      $allDomains[] = normalizeDomainRow($item, $rowNumber);
    }

    $captchaVerified = true;
    $pagesFetched++;
    $currentPage++;

    if ($pagesFetched < $config['page_limit'] && $currentPage <= $totalPages) {
      $delay = $config['delay'];
      sleep((int) $delay);
      usleep((int) (($delay - (int) $delay) * 1_000_000));
    }
  }

  if ($allDomains === []) {
    throw new RuntimeException('هیچ رکوردی استخراج نشد.');
  }

  return ['rows' => $allDomains];
}

function promptCaptchaCli(EnamadClient $client, array &$captchaData, ?string $providedCode): string
{
  if ($providedCode !== null && $providedCode !== '') {
    return trim($providedCode);
  }

  $imagePath = __DIR__ . '/captcha.jpg';
  $client->saveCaptchaImage($captchaData, $imagePath);
  echo "تصویر کپچا: {$imagePath}\n";
  echo 'کد کپچا را وارد کنید: ';

  return trim((string) fgets(STDIN));
}

function renderWebPage(): void
{
  header('Content-Type: text/html; charset=UTF-8');
  echo <<<'HTML'
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>استخراج فهرست اینماد</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: Tahoma, Arial, sans-serif; background: #f4f6f8; margin: 0; padding: 24px; color: #222; }
    .box { max-width: 640px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
    h1 { margin-top: 0; font-size: 1.4rem; color: #2a93c9; }
    label { display: block; margin: 12px 0 6px; font-weight: bold; }
    input, select { width: 100%; padding: 10px; border: 1px solid #ccd6dd; border-radius: 8px; font-size: 1rem; }
    button { margin-top: 16px; width: 100%; padding: 12px; background: #2a93c9; color: #fff; border: 0; border-radius: 8px; font-size: 1rem; cursor: pointer; }
    button:disabled { background: #94c5e0; cursor: not-allowed; }
    button:hover:not(:disabled) { background: #237aa8; }
    .captcha-box { display: flex; gap: 12px; align-items: center; margin-top: 8px; }
    .captcha-box img { border: 1px solid #ccd6dd; border-radius: 8px; height: 50px; min-width: 120px; background: #f9fafb; }
    .error { background: #fdecea; color: #b42318; padding: 12px; border-radius: 8px; display: none; }
    .success { background: #ecfdf3; color: #027a48; padding: 12px; border-radius: 8px; display: none; }
    .hint { color: #667085; font-size: .9rem; margin-top: 8px; }
    .progress { margin-top: 16px; display: none; }
    .progress-bar { height: 8px; background: #e5e7eb; border-radius: 4px; overflow: hidden; }
    .progress-fill { height: 100%; width: 0; background: #2a93c9; transition: width .3s; }
    .progress-text { margin-top: 8px; font-size: .9rem; color: #475467; }
    a { color: #2a93c9; }
    .refresh-btn { width: auto; margin: 0; padding: 8px 12px; font-size: .85rem; }
  </style>
</head>
<body>
<div class="box">
  <h1>استخراج فهرست دارندگان اینماد</h1>
  <p id="error" class="error"></p>
  <p id="success" class="success"></p>

  <form id="extractForm">
    <label>کد امنیتی</label>
    <div class="captcha-box">
      <img id="captchaImg" alt="کپچا">
      <button type="button" class="refresh-btn" id="refreshCaptcha">تازه‌سازی</button>
      <input type="text" id="captcha" maxlength="5" autocomplete="off" required placeholder="کد تصویر">
    </div>
    <input type="hidden" id="cptToken">

    <label for="format">فرمت خروجی</label>
    <select id="format">
      <option value="csv">CSV (اکسل)</option>
      <option value="json">JSON</option>
    </select>

    <label for="limit">تعداد صفحه</label>
    <input type="number" id="limit" min="1" value="1" required>
    <p class="hint">هر صفحه ۳۰ رکورد. برای تست اول عدد ۱ بگذارید.</p>

    <label for="output">نام فایل خروجی</label>
    <input type="text" id="output" value="enamad_domains.csv">

    <div class="progress" id="progress">
      <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
      <div class="progress-text" id="progressText"></div>
    </div>

    <button type="submit" id="submitBtn">شروع استخراج</button>
  </form>
</div>
<script>
const $ = (id) => document.getElementById(id);

function showError(msg) {
  $('error').textContent = msg;
  $('error').style.display = 'block';
  $('success').style.display = 'none';
}

function showSuccess(msg) {
  $('success').innerHTML = msg;
  $('success').style.display = 'block';
  $('error').style.display = 'none';
}

async function loadCaptcha() {
  const res = await fetch('?action=captcha');
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'خطا در دریافت کپچا');
  $('captchaImg').src = 'data:image/jpeg;base64,' + data.captha;
  $('cptToken').value = data.cptToken;
  $('captcha').value = '';
}

$('refreshCaptcha').addEventListener('click', () => loadCaptcha().catch(e => showError(e.message)));
loadCaptcha().catch(e => showError(e.message));

$('extractForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('submitBtn').disabled = true;
  $('progress').style.display = 'block';
  showError('');
  showSuccess('');

  const limit = parseInt($('limit').value, 10) || 1;
  const captcha = $('captcha').value.trim();
  const cptToken = $('cptToken').value;
  const allRows = [];
  let verified = false;
  let totalPages = limit;

  try {
    for (let page = 1; page <= limit; page++) {
      $('progressText').textContent = `در حال دریافت صفحه ${page} از ${limit}...`;
      $('progressFill').style.width = `${Math.round(((page - 1) / limit) * 100)}%`;

      const res = await fetch('?action=fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          page,
          captcha: page === 1 ? captcha : '',
          cpt_token: page === 1 ? cptToken : '',
          verified,
        }),
      });

      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'خطای نامشخص');

      verified = true;
      totalPages = data.totalPages;
      allRows.push(...data.rows);

      if (page >= totalPages) break;
      await new Promise(r => setTimeout(r, 800));
    }

    $('progressFill').style.width = '100%';
    $('progressText').textContent = `در حال ذخیره ${allRows.length} رکورد...`;

    const saveRes = await fetch('?action=save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        rows: allRows,
        format: $('format').value,
        output: $('output').value,
      }),
    });
    const saveData = await saveRes.json();
    if (!saveData.ok) throw new Error(saveData.error || 'خطا در ذخیره فایل');

    showSuccess(`${saveData.count} رکورد ذخیره شد. <a href="${saveData.file}" download>دانلود فایل</a>`);
    await loadCaptcha();
  } catch (err) {
    showError(err.message);
    await loadCaptcha();
  } finally {
    $('submitBtn').disabled = false;
  }
});
</script>
</body>
</html>
HTML;
}

final class EnamadClient
{
  private bool $sessionReady = false;

  public function __construct(private readonly string $cookieFile)
  {
    if (!is_writable(dirname($this->cookieFile)) && !file_exists($this->cookieFile)) {
      throw new RuntimeException('مسیر ذخیره کوکی قابل نوشتن نیست.');
    }
  }

  /** @return array{captha: string, cptToken: string} */
  public function refreshCaptcha(): array
  {
    $this->warmSession();
    $response = $this->request('POST', 'refreshCapt', [], 'application/json; charset=UTF-8', '{}');
    $data = parseJsonResponse($response, 'refreshCapt');

    if (empty($data['cptToken'])) {
      throw new RuntimeException('دریافت کپچا ناموفق بود.');
    }

    return $data;
  }

  /** @return array<string, mixed> */
  public function getDomainList(
    int $page,
    string $token,
    bool $checkCaptchaBypass,
    string $captchaCode = '',
    array $filters = []
  ): array {
    $this->warmSession();

    $payload = [
      's#ms-domain-address' => $filters['domain'] ?? '',
      's#ms-persian-name' => $filters['name'] ?? '',
      's#ms-product-service-id-enc' => $filters['service'] ?? '',
      's#mi-rating' => $filters['rating'] ?? '-1',
      's#ms-province-id-enc' => $filters['province'] ?? '',
      's#ms-city-id-enc' => $filters['city'] ?? '',
      'Capt' => $captchaCode,
      'Csearch' => '',
      'page' => (string) $page,
      'token' => $token,
      'cptToken' => $token,
      'checkcapga' => $checkCaptchaBypass ? '1' : '0',
    ];

    $response = $this->request(
      'POST',
      'getDomainList',
      $payload,
      'application/x-www-form-urlencoded;charset=utf-8'
    );

    return parseJsonResponse($response, 'getDomainList');
  }

  public function saveCaptchaImage(array $captchaData, string $path): void
  {
    $base64 = $captchaData['captha'] ?? $captchaData['captcha'] ?? '';
    if ($base64 === '') {
      throw new RuntimeException('تصویر کپچا در پاسخ نبود.');
    }

    $binary = base64_decode($base64, true);
    if ($binary === false) {
      throw new RuntimeException('decode تصویر کپچا ناموفق بود.');
    }

    file_put_contents($path, $binary);
  }

  private function warmSession(): void
  {
    if ($this->sessionReady) {
      return;
    }

    $this->request('GET', 'DomainListForMIMT');
    $this->sessionReady = true;
  }

  private function request(
    string $method,
    string $path,
    array $fields = [],
    string $contentType = 'application/x-www-form-urlencoded;charset=utf-8',
    ?string $rawBody = null
  ): string {
    $url = rtrim(BASE_URL, '/') . '/' . ltrim($path, '/');
    $ch = curl_init($url);

    $headers = [
      'Accept: application/json, text/plain, */*',
      'Accept-Language: fa-IR,fa;q=0.9,en;q=0.8',
      'Origin: https://enamad.ir',
      'Referer: https://enamad.ir/DomainListForMIMT',
      'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ];

    if ($method === 'GET') {
      $postFields = null;
    } elseif ($rawBody !== null) {
      $headers[] = 'Content-Type: ' . $contentType;
      $postFields = $rawBody;
    } else {
      $headers[] = 'Content-Type: ' . $contentType;
      $postFields = buildFormBody($fields);
    }

    $curlOptions = [
      CURLOPT_RETURNTRANSFER => true,
      CURLOPT_FOLLOWLOCATION => true,
      CURLOPT_TIMEOUT => 90,
      CURLOPT_CONNECTTIMEOUT => 20,
      CURLOPT_CUSTOMREQUEST => $method,
      CURLOPT_HTTPHEADER => $headers,
      CURLOPT_COOKIEJAR => $this->cookieFile,
      CURLOPT_COOKIEFILE => $this->cookieFile,
      CURLOPT_ENCODING => '',
      CURLOPT_SSL_VERIFYPEER => true,
      CURLOPT_SSL_VERIFYHOST => 2,
    ];

    if ($postFields !== null) {
      $curlOptions[CURLOPT_POSTFIELDS] = $postFields;
    }

    $caBundle = resolveCaBundle();
    if ($caBundle !== null) {
      $curlOptions[CURLOPT_CAINFO] = $caBundle;
    } else {
      $curlOptions[CURLOPT_SSL_VERIFYPEER] = false;
      $curlOptions[CURLOPT_SSL_VERIFYHOST] = 0;
    }

    curl_setopt_array($ch, $curlOptions);

    $body = curl_exec($ch);
    $error = curl_error($ch);
    $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($body === false) {
      throw new RuntimeException("خطای cURL: {$error}");
    }

    if ($status >= 400) {
      $snippet = mb_substr(trim(strip_tags((string) $body)), 0, 150);
      throw new RuntimeException("HTTP {$status} برای {$path}: {$snippet}");
    }

    return (string) $body;
  }
}

/** @param array<string, string> $fields */
function buildFormBody(array $fields): string
{
  $parts = [];
  foreach ($fields as $key => $value) {
    $parts[] = rawurlencode($key) . '=' . rawurlencode($value);
  }

  return implode('&', $parts);
}

/** @return array<string, mixed> */
function parseJsonResponse(string $response, string $context): array
{
  $trimmed = trim($response);
  if (str_starts_with($trimmed, "\xEF\xBB\xBF")) {
    $trimmed = substr($trimmed, 3);
  }

  $data = json_decode($trimmed, true);
  if (is_array($data)) {
    return $data;
  }

  $snippet = mb_substr(trim(strip_tags($trimmed)), 0, 200);
  throw new RuntimeException("پاسخ نامعتبر از {$context}: {$snippet}");
}

function resolveCaBundle(): ?string
{
  $candidates = [
    'C:\\laragon\\etc\\ssl\\cacert.pem',
    'D:\\laragon\\etc\\ssl\\cacert.pem',
    __DIR__ . DIRECTORY_SEPARATOR . 'cacert.pem',
  ];

  foreach (['curl.cainfo', 'openssl.cafile'] as $iniKey) {
    $iniPath = ini_get($iniKey);
    if (is_string($iniPath) && $iniPath !== '' && is_file($iniPath)) {
      array_unshift($candidates, $iniPath);
    }
  }

  foreach (array_unique($candidates) as $path) {
    if (is_file($path)) {
      return $path;
    }
  }

  return null;
}

/** @param array<string, mixed> $item */
function normalizeDomainRow(array $item, int $rowNumber): array
{
  return [
    'row' => $rowNumber,
    'id' => $item['id'] ?? '',
    'code' => $item['code'] ?? '',
    'domain' => $item['domain_address'] ?? '',
    'business_name' => $item['persian_name'] ?? '',
    'province' => $item['province'] ?? '',
    'city' => $item['city'] ?? '',
    'rating' => $item['rating'] ?? 0,
    'approve_date' => $item['approve_date'] ?? '',
    'expire_date' => $item['expire_date'] ?? '',
    'trustseal_url' => buildTrustsealUrl($item),
  ];
}

/** @param array<string, mixed> $item */
function buildTrustsealUrl(array $item): string
{
  $id = $item['id'] ?? '';
  $code = $item['code'] ?? '';

  if ($id === '' || $code === '') {
    return '';
  }

  return 'https://trustseal.enamad.ir/?id=' . rawurlencode((string) $id) . '&code=' . rawurlencode((string) $code);
}

function needsCaptcha(string $message): bool
{
  $keywords = ['کپچا', 'کد امنیتی', 'captcha', 'امنیتی', 'وارد نمایید'];
  $lower = mb_strtolower($message, 'UTF-8');

  foreach ($keywords as $keyword) {
    if (mb_strpos($lower, mb_strtolower($keyword, 'UTF-8')) !== false) {
      return true;
    }
  }

  return false;
}

/** @param list<array<string, mixed>> $rows */
function saveOutput(array $rows, string $file, string $format): void
{
  if ($rows === []) {
    throw new RuntimeException('داده‌ای برای ذخیره وجود ندارد.');
  }

  if ($format === 'json') {
    $json = json_encode($rows, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    if ($json === false) {
      throw new RuntimeException('ساخت JSON ناموفق بود.');
    }
    file_put_contents($file, $json . PHP_EOL);
    return;
  }

  $fp = fopen($file, 'wb');
  if ($fp === false) {
    throw new RuntimeException("نمی‌توان فایل {$file} را نوشت.");
  }

  fwrite($fp, "\xEF\xBB\xBF");

  $headers = array_keys($rows[0]);
  fputcsv($fp, $headers);

  foreach ($rows as $row) {
    fputcsv($fp, array_values($row));
  }

  fclose($fp);
}
