<?php
/**
 * Plugin Name: My MCP Abilities (Packaged)
 * Description: Abilities API + MCP Adapter を同梱した読み取り専用ツール群（パッケージ配布向け）
 * Version: 0.1.9
 * Requires at least: 6.0
 * Requires PHP: 8.0
 */

if ( ! defined('ABSPATH') ) exit;

// Autoloader（Jetpack Autoloader があれば優先）
$base = __DIR__ . '/vendor/';
if ( file_exists($base . 'autoload_packages.php') ) {
    require_once $base . 'autoload_packages.php';
} elseif ( file_exists($base . 'autoload.php') ) {
    require_once $base . 'autoload.php';
}

// Initialize the MCP Adapter so its REST routes (and default server) are registered.
add_action( 'plugins_loaded', function () {
    $class = '\\WP\\MCP\\Core\\McpAdapter';
    if ( class_exists( $class ) ) {
        $class::instance();
    }

    // Force Abilities registry bootstrap so abilities_api_init definitely fires.
    if ( class_exists( 'WP_Abilities_Registry' ) ) {
        WP_Abilities_Registry::get_instance();
    }
});

// ---- 依存パッケージを確実にロード（ホスティング環境でオートローダーが動かない場合の保険）----
if ( ! function_exists( 'wp_register_ability' ) ) {
    $abilities_bootstrap = __DIR__ . '/vendor/wordpress/abilities-api/includes/bootstrap.php';
    if ( file_exists( $abilities_bootstrap ) ) {
        require_once $abilities_bootstrap;
    }
}

if ( ! class_exists( \WP\MCP\Plugin::class ) ) {
    $mcp_adapter = __DIR__ . '/vendor/wordpress/mcp-adapter/mcp-adapter.php';
    if ( file_exists( $mcp_adapter ) ) {
        require_once $mcp_adapter;
    }
}
// ---- ここまで依存ロード ----

/**
 * 出力整形（投稿/固定ページ/添付 兼用）
 */
if ( ! function_exists('mma_sanitize_post_array') ) {
    function mma_sanitize_post_array( $p ) {
        if ( is_numeric($p) ) $p = get_post($p);
        return [
            'ID'       => (int) $p->ID,
            'title'    => get_the_title($p),
            'modified' => get_post_modified_time('c', true, $p),
            'link'     => get_permalink($p),
            'type'     => $p->post_type,
            'status'   => $p->post_status,
        ];
    }
}

/**
 * Abilities 登録（読み取り専用ツール群）
 * - abilities_api_init が本来のフック
 * - 念のため init からも呼び出し、二重登録は静的フラグで防ぐ
 */
if ( ! function_exists( 'mma_register_marketing_abilities' ) ) {
    function mma_register_marketing_abilities() {
        static $registered = false;
        if ( $registered ) return;
        if ( ! function_exists( 'wp_register_ability' ) ) return;

        // Ensure registry exists (should already be bootstrapped).
        if ( class_exists( 'WP_Abilities_Registry' ) ) {
            WP_Abilities_Registry::get_instance();
        }

        // Register ability category first (required by Abilities API).
        if ( function_exists('wp_register_ability_category') ) {
            wp_register_ability_category('marketing', [
                'label'       => 'Marketing',
                'description' => 'Read-only marketing/content utilities.',
            ]);
        }

        // Helper meta shared by read-only abilities so they are exposed via REST.
        $readonly_meta = [
            'show_in_rest' => true,
            'annotations'  => ['readonly' => true],
        ];

        // 1) 投稿一覧（既定は公開のみ）
        wp_register_ability('marketing/get-posts', [
            'label'       => 'Get Recent Posts',
            'description' => 'Retrieve recent posts (read-only; publish by default).',
            'category'    => 'marketing',
            'meta'        => $readonly_meta,
            'input_schema' => [
                'type'       => 'object',
                'properties' => [
                    'number' => ['type'=>'integer','minimum'=>1,'maximum'=>50,'default'=>5],
                    'status' => ['type'=>'string','enum'=>['publish','private'],'default'=>'publish'],
                ],
            ],
            'output_schema' => [
                'type'  => 'array',
                'items' => [
                    'type'       => 'object',
                    'properties' => [
                        'ID'=>['type'=>'integer'],
                        'title'=>['type'=>'string'],
                        'modified'=>['type'=>'string'],
                        'link'=>['type'=>'string'],
                        'type'=>['type'=>'string'],
                        'status'=>['type'=>'string'],
                    ],
                    'required' => ['ID','title','modified','link'],
                ],
            ],
            'execute_callback' => function ($in) {
                $posts = get_posts([
                    'numberposts' => $in['number'] ?? 5,
                    'post_status' => $in['status'] ?? 'publish',
                ]);
                return array_map('mma_sanitize_post_array', $posts);
            },
            'permission_callback' => function ($in) {
                $status = $in['status'] ?? 'publish';
                return ($status === 'publish') ? true : current_user_can('read_private_posts');
            },
        ]);

    // 2) 投稿検索（全文検索）
        wp_register_ability('marketing/search-posts', [
        'label'       => 'Search Posts',
        'description' => 'Full-text search for posts by keyword (read-only).',
        'category'    => 'marketing',
        'meta'        => $readonly_meta,
        'input_schema' => [
            'type'       => 'object',
            'properties' => [
                's'      => ['type'=>'string'],
                'number' => ['type'=>'integer','minimum'=>1,'maximum'=>50,'default'=>5],
                'status' => ['type'=>'string','enum'=>['publish','private'],'default'=>'publish'],
            ],
            'required' => ['s'],
        ],
        'output_schema' => [
            'type'  => 'array',
            'items' => [
                'type'       => 'object',
                'properties' => [
                    'ID'=>['type'=>'integer'],
                    'title'=>['type'=>'string'],
                    'modified'=>['type'=>'string'],
                    'link'=>['type'=>'string'],
                ],
                'required' => ['ID','title','modified','link'],
            ],
        ],
        'execute_callback' => function ($in) {
            $q = new WP_Query([
                's'            => $in['s'] ?? '',
                'posts_per_page' => $in['number'] ?? 5,
                'post_status'  => $in['status'] ?? 'publish',
            ]);
            return array_map('mma_sanitize_post_array', $q->posts);
        },
        'permission_callback' => function ($in) {
            return (($in['status'] ?? 'publish') === 'publish') ? true : current_user_can('read_private_posts');
        },
    ]);

    // 3) 固定ページ
        wp_register_ability('marketing/get-pages', [
        'label'       => 'Get Pages',
        'description' => 'List published pages (read-only).',
        'category'    => 'marketing',
        'meta'        => $readonly_meta,
        'input_schema' => [
            'type'=>'object',
            'properties'=>[
                'number'=>['type'=>'integer','minimum'=>1,'maximum'=>100,'default'=>10],
            ],
        ],
        'output_schema' => [
            'type'=>'array',
            'items'=>[
                'type'=>'object',
                'properties'=>[
                    'ID'=>['type'=>'integer'],
                    'title'=>['type'=>'string'],
                    'modified'=>['type'=>'string'],
                    'link'=>['type'=>'string'],
                ],
                'required'=>['ID','title','modified','link'],
            ],
        ],
        'execute_callback' => function ($in) {
            $pages = get_posts([
                'post_type'   => 'page',
                'numberposts' => $in['number'] ?? 10,
                'post_status' => 'publish',
            ]);
            return array_map('mma_sanitize_post_array', $pages);
        },
        'permission_callback' => fn()=>true,
    ]);

    // 4) メディア
        wp_register_ability('marketing/get-media', [
        'label'       => 'Get Media',
        'description' => 'List media library items (read-only).',
        'category'    => 'marketing',
        'meta'        => $readonly_meta,
        'input_schema' => [
            'type'=>'object',
            'properties'=>[
                'mime'  => ['type'=>'string','default'=>'image'],
                'number'=> ['type'=>'integer','minimum'=>1,'maximum'=>100,'default'=>10],
            ],
        ],
        'output_schema' => [
            'type'=>'array',
            'items'=>[
                'type'=>'object',
                'properties'=>[
                    'ID'=>['type'=>'integer'],
                    'title'=>['type'=>'string'],
                    'modified'=>['type'=>'string'],
                    'link'=>['type'=>'string'],
                    'mime'=>['type'=>'string'],
                ],
                'required'=>['ID','title','modified','link','mime'],
            ],
        ],
        'execute_callback' => function ($in) {
            $mime  = $in['mime'] ?? 'image';
            $items = get_posts([
                'post_type'    => 'attachment',
                'numberposts'  => $in['number'] ?? 10,
                'post_mime_type' => $mime,
                'post_status'  => 'inherit',
            ]);
            return array_map(function($p){
                $arr = mma_sanitize_post_array($p);
                $arr['mime'] = get_post_mime_type($p);
                $arr['link'] = wp_get_attachment_url($p->ID);
                return $arr;
            }, $items);
        },
        'permission_callback' => fn()=>true,
    ]);

    // 5) カテゴリ
        wp_register_ability('marketing/get-categories', [
        'label'       => 'Get Categories',
        'description' => 'List post categories (read-only).',
        'category'    => 'marketing',
        'meta'        => $readonly_meta,
        'input_schema' => [
            'type'=>'object',
            'properties'=>[
                'hide_empty'=>['type'=>'boolean','default'=>true],
            ],
        ],
        'output_schema' => [
            'type'=>'array',
            'items'=>[
                'type'=>'object',
                'properties'=>[
                    'term_id'=>['type'=>'integer'],
                    'name'=>['type'=>'string'],
                    'slug'=>['type'=>'string'],
                    'count'=>['type'=>'integer'],
                ],
                'required'=>['term_id','name','slug'],
            ],
        ],
        'execute_callback' => function ($in) {
            $terms = get_terms(['taxonomy'=>'category','hide_empty'=>$in['hide_empty'] ?? true]);
            return array_map(fn($t)=>[
                'term_id'=>$t->term_id,'name'=>$t->name,'slug'=>$t->slug,'count'=>$t->count
            ], $terms);
        },
        'permission_callback' => fn()=>true,
    ]);

    // 6) タグ
        wp_register_ability('marketing/get-tags', [
        'label'       => 'Get Tags',
        'description' => 'List post tags (read-only).',
        'category'    => 'marketing',
        'meta'        => $readonly_meta,
        'input_schema' => [
            'type'=>'object',
            'properties'=>[
                'hide_empty'=>['type'=>'boolean','default'=>true],
            ],
        ],
        'output_schema' => [
            'type'=>'array',
            'items'=>[
                'type'=>'object',
                'properties'=>[
                    'term_id'=>['type'=>'integer'],
                    'name'=>['type'=>'string'],
                    'slug'=>['type'=>'string'],
                    'count'=>['type'=>'integer'],
                ],
                'required'=>['term_id','name','slug'],
            ],
        ],
        'execute_callback' => function ($in) {
            $terms = get_terms(['taxonomy'=>'post_tag','hide_empty'=>$in['hide_empty'] ?? true]);
            return array_map(fn($t)=>[
                'term_id'=>$t->term_id,'name'=>$t->name,'slug'=>$t->slug,'count'=>$t->count
            ], $terms);
        },
        'permission_callback' => fn()=>true,
    ]);

    // 7) コメント
        wp_register_ability('marketing/get-comments', [
        'label'       => 'Get Recent Comments',
        'description' => 'List recent approved comments (read-only).',
        'category'    => 'marketing',
        'meta'        => $readonly_meta,
        'input_schema' => [
            'type'=>'object',
            'properties'=>[
                'number'=>['type'=>'integer','minimum'=>1,'maximum'=>100,'default'=>10],
                'post_id'=>['type'=>'integer'],
            ],
        ],
        'output_schema' => [
            'type'=>'array',
            'items'=>[
                'type'=>'object',
                'properties'=>[
                    'comment_ID'=>['type'=>'integer'],
                    'post_ID'=>['type'=>'integer'],
                    'author'=>['type'=>'string'],
                    'date'=>['type'=>'string'],
                    'excerpt'=>['type'=>'string'],
                ],
                'required'=>['comment_ID','post_ID','author','date','excerpt'],
            ],
        ],
        'execute_callback' => function ($in) {
            $args = ['number'=>$in['number'] ?? 10, 'status'=>'approve'];
            if (!empty($in['post_id'])) $args['post_id'] = (int) $in['post_id'];
            $comments = get_comments($args);
            return array_map(fn($c)=>[
                'comment_ID'=>(int)$c->comment_ID,
                'post_ID'=>(int)$c->comment_post_ID,
                'author'=>$c->comment_author,
                'date'=>mysql2date('c', $c->comment_date),
                'excerpt'=>wp_trim_words($c->comment_content, 30),
            ], $comments);
        },
        'permission_callback' => fn()=>true,
        ]);

        $registered = true;
    }
}

add_action( 'abilities_api_init', 'mma_register_marketing_abilities' );
// Fallback: if abilities_api_init didn’t fire for some reason, try after core init.
add_action( 'init', 'mma_register_marketing_abilities', 20 );

/**
 * MCP Adapter サーバ定義（Abilities をこのサーバで公開）
 */
add_action('mcp_adapter_init', function($adapter){
    if ( ! is_object($adapter) || ! method_exists($adapter, 'create_server') ) return;

    $adapter->create_server(
        'marketing-ro-server',            // サーバID（エンドポイント名に使われる）
        'marketing',                      // ドメイン
        'mcp',                            // プロトコル
        'Marketing Readonly MCP Server',  // 表示名
        'Read-only marketing/content tools', // 説明
        '0.1.0',                          // バージョン
        // Prefer HttpTransport (added in mcp-adapter >=0.3). Fallback to RestTransport for older bundles.
        [ class_exists(\WP\MCP\Transport\HttpTransport::class)
            ? \WP\MCP\Transport\HttpTransport::class
            : \WP\MCP\Transport\Http\RestTransport::class ],
        \WP\MCP\Infrastructure\ErrorHandling\ErrorLogMcpErrorHandler::class,
        null, // observability handler (null = default)
        [
            'marketing/get-posts',
            'marketing/search-posts',
            'marketing/get-pages',
            'marketing/get-media',
            'marketing/get-categories',
            'marketing/get-tags',
            'marketing/get-comments',
        ]
    );
});
